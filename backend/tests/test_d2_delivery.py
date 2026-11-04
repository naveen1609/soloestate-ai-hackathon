"""D2 durable delivery tests (DLV matrix + receipt identity). Rollback by default."""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import crm_schemas as s
from app import dispatch, receptionist
from app import whatsapp as wa
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM, CRMError, scalar
from app.database import connect
from app.policies import DAY_CAP_DEFAULT, set_stop
from app.services.meta_model import ModelResult

pytestmark = pytest.mark.d2_database


class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


class StubSubmit:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, recipient, text):
        self.calls.append((recipient, text))
        outcome = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _enable(b, categories=None):
    from app.policies import ROUTINE_CATEGORIES, update_reply_policy
    with b['conn'].cursor() as cur:
        version = cur.execute("SELECT version FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()[0]
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=version, enabled=True,
        categories=list(categories if categories is not None else ROUTINE_CATEGORIES),
        reason='Fictional D2 test enable'))


@pytest.fixture
def dv(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2dv-' + str(uuid4())
            b = dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
                     key=lambda: prefix + ':' + str(uuid4()))
            _enable(b)
            yield b


def _inbound(b, sender, body='Fictional hello', event_time=None):
    from app import identity_links
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.DLV' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        response = {'provider_ts': (event_time or now).isoformat(),
                    'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                         'link_id': str(link['id'])}}
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
        message = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), convo['id'], seq, 'customer', body, op_key)).fetchone()
        cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (convo['id'],)).fetchone()
    return dict(link=link, lead=lead, convo=convo, message=message, wamid=wamid)


def _outbox(b, message_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',
                           (str(message_id),)).fetchone()


def _attempts(b, outbox_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE outbox_id=%s ORDER BY attempt_no',
                           (outbox_id,)).fetchall()


def _used(b, channel='whatsapp'):
    with b['conn'].cursor() as cur:
        row = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE channel=%s ORDER BY day DESC LIMIT 1',
                          (channel,)).fetchone()
        return row[0] if row else 0


def _welcome_turn():
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category='welcome',
                           segments=[dict(phrase_id='ACK_RECEIVED', args={})]))


def test_dlv01_inline_dispatch_to_read(dv):
    b = dv
    box = _inbound(b, '15550003111')
    stub = StubModel([_welcome_turn()])
    result = receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'], model=stub)
    assert result['replied'] is True and result['delivery']['status'] == 'staged'
    row = _outbox(b, box['message']['id'])
    assert row['destination'] == '+15550003111' and row['destination_basis'] == 'in_thread'
    assert row['category'] == 'welcome' and _used(b) == 1
    submit = StubSubmit(['wamid.DLV01'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='w1', submit=submit)
    assert driven == {'status': 'sent', 'provider_msg_id': 'wamid.DLV01'}
    assert submit.calls == [('+15550003111', result['payload'])]
    assert len(_attempts(b, row['id'])) == 1
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV01', 'sent', None, {})['duplicate'] is True
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV01', 'delivered', None, {}) == {
            'matched': True, 'advanced': 'delivered'}
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV01', 'delivered', None, {})['duplicate'] is True
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV01', 'read', None, {}) == {
            'matched': True, 'advanced': 'read'}
    assert _outbox(b, box['message']['id'])['status'] == 'read'
    assert _used(b) == 1


def test_dlv02_staged_crash_redrives_same_attempt(dv):
    b = dv
    box = _inbound(b, '15550003112')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    first = _attempts(b, row['id'])[0]
    with b['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.delivery_attempts SET lease_owner='dead-worker', "
                    "lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (first['id'],))
    submit = StubSubmit(['wamid.DLV02'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w2', submit=submit)['status'] == 'sent'
    attempts = _attempts(b, row['id'])
    assert len(attempts) == 1 and str(attempts[0]['id']) == str(first['id'])
    assert len(submit.calls) == 1


def test_dlv03_submitting_crash_is_unknown_without_resume(dv):
    b = dv
    box = _inbound(b, '15550003113')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    submit = StubSubmit([RuntimeError('Fictional crash')])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w3', submit=submit) == {'status': 'unknown'}
    assert _outbox(b, box['message']['id'])['status'] == 'unknown'
    assert _used(b) == 1
    assert dispatch.drive_row(b['factory'], row['id'], worker='w3b', submit=submit) == {'status': 'unknown'}
    assert len(submit.calls) == 1 and len(_attempts(b, row['id'])) == 1


def test_dlv04_owner_resend_discloses_duplicate_risk(dv):
    b = dv
    box = _inbound(b, '15550003114')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    submit = StubSubmit([dispatch.SubmitUnknown('timeout')])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w4', submit=submit) == {'status': 'unknown'}
    resolved = dispatch.resolve_unknown(
        b['crm'], row['id'], dispatch.OutboxResolve(request_key=b['key'](), action='authorize_resend',
                                                    reason='Fictional owner resend'))
    assert resolved['duplicate_risk'] is True and 'twice' in resolved['disclosure']
    attempts = _attempts(b, row['id'])
    assert [a['status'] for a in attempts] == ['superseded', 'staged']
    assert _outbox(b, box['message']['id'])['resend_count'] == 1
    submit2 = StubSubmit(['wamid.DLV04'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w4b', submit=submit2)['status'] == 'sent'
    assert _used(b) == 1


def test_dlv04b_resolve_sent_and_abandon(dv):
    b = dv
    box = _inbound(b, '15550003115')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    dispatch.drive_row(b['factory'], row['id'], worker='w5',
                       submit=StubSubmit([dispatch.SubmitUnknown('timeout')]))
    with pytest.raises(CRMError, match='evidence'):
        dispatch.resolve_unknown(b['crm'], row['id'], dispatch.OutboxResolve(
            request_key=b['key'](), action='mark_resolved_sent', reason='Fictional resolve'))
    resolved = dispatch.resolve_unknown(
        b['crm'], row['id'], dispatch.OutboxResolve(request_key=b['key'](), action='mark_resolved_sent',
                                                    reason='Fictional resolve',
                                                    evidence='Provider dashboard shows delivery'))
    assert resolved['duplicate_risk'] is False
    assert _outbox(b, box['message']['id'])['status'] == 'sent'
    assert _used(b) == 1
    box2 = _inbound(b, '15550003116')
    receptionist.respond_turn(b['factory'], box2['convo']['id'], box2['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row2 = _outbox(b, box2['message']['id'])
    dispatch.drive_row(b['factory'], row2['id'], worker='w5b',
                       submit=StubSubmit([dispatch.SubmitUnknown('timeout')]))
    abandoned = dispatch.resolve_unknown(
        b['crm'], row2['id'], dispatch.OutboxResolve(request_key=b['key'](), action='abandon',
                                                     reason='Fictional abandon'))
    assert abandoned['duplicate_risk'] is False
    assert _outbox(b, box2['message']['id'])['status'] == 'skipped'


def test_dlv_stop_before_staging_skips(dv):
    b = dv
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=True, reason='Fictional D2 stop'))
    box = _inbound(b, '15550003117')
    result = receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                                       model=StubModel([_welcome_turn()]))
    assert result['replied'] is True
    row = _outbox(b, box['message']['id'])
    assert row['status'] == 'skipped' and row['skip_reason'] == 'emergency_stop'
    assert _attempts(b, row['id']) == []
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=False, reason='Fictional D2 lift'))


def test_dlv_stop_between_staging_and_transition_blocks_and_refunds(dv):
    b = dv
    box = _inbound(b, '15550003118')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert _used(b) == 1
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=True, reason='Fictional D2 stop'))
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='w7', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['emergency_stop']
    assert submit.calls == []
    assert _outbox(b, box['message']['id'])['status'] == 'queued'
    assert _used(b) == 0
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=False, reason='Fictional D2 lift'))
    assert dispatch.drive_row(b['factory'], row['id'], worker='w7b', submit=submit)['status'] == 'sent'


def test_dlv_definitive_failure_retries_then_dead(dv):
    b = dv
    box = _inbound(b, '15550003119')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    submit = StubSubmit([dispatch.SubmitFailed('invalid')])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w8', submit=submit) == {'status': 'dead'}
    assert len(submit.calls) == 3
    assert [a['status'] for a in _attempts(b, row['id'])] == ['failed', 'failed', 'failed']
    assert _used(b) == 0
    requeued = dispatch.requeue(b['crm'], row['id'], dispatch.OutboxRequeue(
        request_key=b['key'](), reason='Fictional requeue'))
    assert requeued['record']['status'] == 'staged'
    submit2 = StubSubmit(['wamid.DLV08'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w8b', submit=submit2)['status'] == 'sent'


def test_dlv_unknown_with_id_advances_on_late_receipt(dv):
    b = dv
    box = _inbound(b, '15550003120')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    submit = StubSubmit([dispatch.SubmitUnknown('timeout', 'wamid.DLV09')])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w9', submit=submit) == {'status': 'unknown'}
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV09', 'delivered', None, {}) == {
            'matched': True, 'advanced': 'delivered'}
    assert _outbox(b, box['message']['id'])['status'] == 'delivered'
    assert _attempts(b, row['id'])[0]['status'] == 'acknowledged'
    assert _used(b) == 1
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert dispatch.reconcile_receipt(cur, 'nope-unknown', 'delivered', None, {}) == {
            'matched': False, 'provider_msg_id': 'nope-unknown'}


def test_dlv_receipt_failed_refunds(dv):
    b = dv
    box = _inbound(b, '15550003121')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    dispatch.drive_row(b['factory'], row['id'], worker='w10', submit=StubSubmit(['wamid.DLV10']))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV10', 'failed', None, {}) == {
            'matched': True, 'advanced': 'failed'}
        assert dispatch.reconcile_receipt(cur, 'wamid.DLV10', 'failed', None, {})['duplicate'] is True
    assert _outbox(b, box['message']['id'])['status'] == 'failed'
    assert _used(b) == 0


def test_dlv_duplicate_worker_backs_off(dv):
    b = dv
    box = _inbound(b, '15550003122')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    first = _attempts(b, row['id'])[0]
    with b['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.delivery_attempts SET lease_owner='w11a', "
                    "lease_until=clock_timestamp()+interval '5 minutes' WHERE id=%s", (first['id'],))
    submit = StubSubmit(['wamid.DLV11'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w11b', submit=submit) == {'status': 'busy'}
    assert submit.calls == []
    with b['conn'].cursor() as cur:
        cur.execute('UPDATE soloestate.delivery_attempts SET lease_until=clock_timestamp()-interval '
                    "'1 second' WHERE id=%s", (first['id'],))
    assert dispatch.drive_row(b['factory'], row['id'], worker='w11b', submit=submit)['status'] == 'sent'
    assert len(_attempts(b, row['id'])) == 1


def test_dlv_same_key_conflict_and_replay(dv):
    b = dv
    box = _inbound(b, '15550003123')
    key = dispatch.stable_key('whatsapp', 'reply', str(box['message']['id']))
    payload = {'body': 'Fictional payload', 'catalog_version': CATALOG_VERSION}
    kwargs = dict(request_key=key, channel='whatsapp', purpose='reply', source_id=str(box['message']['id']),
                  lead_id=box['lead']['id'], conversation_id=box['convo']['id'], link_id=box['link']['id'],
                  destination='+15550003123', category='welcome', content_ref='reply:test',
                  catalog_version=CATALOG_VERSION)
    first = dispatch.stage(b['crm'], payload=payload, **kwargs)
    assert first['created'] is True and first['record']['status'] == 'staged'
    replay = dispatch.stage(b['crm'], payload=dict(payload), **kwargs)
    assert replay == {'created': False, 'record': first['record'], 'replayed': True}
    with pytest.raises(CRMError, match='different payload'):
        dispatch.stage(b['crm'], payload=dict(payload, body='Changed'), **kwargs)
    with b['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_outbox WHERE request_key=%s',
                                  (key,)).fetchone()) == 1


def test_dlv_sensitive_category_never_stages(dv):
    b = dv
    box = _inbound(b, '15550003124')
    with pytest.raises(CRMError, match='non-routine'):
        dispatch.stage(b['crm'], request_key=b['key'](), channel='whatsapp', purpose='reply',
                       source_id=str(box['message']['id']), lead_id=box['lead']['id'],
                       conversation_id=box['convo']['id'], link_id=box['link']['id'],
                       destination='+15550003124', category='negotiation',
                       payload={'body': 'x'}, content_ref='reply:test', catalog_version=CATALOG_VERSION)
    with b['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_outbox WHERE source_id=%s',
                                  (str(box['message']['id']),)).fetchone()) == 0


def test_dlv_suppressed_destination_skips(dv):
    b = dv
    box = _inbound(b, '15550003125')
    with b['conn'].cursor() as cur:
        cur.execute("INSERT INTO soloestate.suppression_list(channel,destination,source,evidence_ref) "
                    "VALUES ('whatsapp','+15550003125','inbound_keyword','test')")
    result = receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                                       model=StubModel([_welcome_turn()]))
    assert result['replied'] is True
    row = _outbox(b, box['message']['id'])
    assert row['status'] == 'skipped' and row['skip_reason'] == 'suppressed'


def test_dlv_expired_thread_skips_with_reason(dv):
    b = dv
    box = _inbound(b, '15550003126', event_time=datetime.now(timezone.utc) - timedelta(hours=49))
    result = receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                                       model=StubModel([_welcome_turn()]))
    assert result['replied'] is True
    row = _outbox(b, box['message']['id'])
    assert row['status'] == 'skipped' and row['skip_reason'] == 'thread_expired'


def test_dlv_new_key_cannot_bypass_unknown(dv):
    b = dv
    box = _inbound(b, '15550003127')
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    submit = StubSubmit([dispatch.SubmitUnknown('timeout')])
    assert dispatch.drive_row(b['factory'], row['id'], worker='w16', submit=submit) == {'status': 'unknown'}
    assert dispatch.drive_row(b['factory'], row['id'], worker='w16b', submit=submit) == {'status': 'unknown'}
    replay = dispatch.stage(b['crm'], request_key=row['request_key'], channel='whatsapp', purpose='reply',
                            source_id=str(box['message']['id']), lead_id=box['lead']['id'],
                            conversation_id=box['convo']['id'], link_id=box['link']['id'],
                            destination='+15550003127', category='welcome', payload=dict(row['payload']),
                            content_ref='reply:test', catalog_version=CATALOG_VERSION)
    assert replay['created'] is False and replay['record']['status'] == 'unknown'
    assert len(_attempts(b, row['id'])) == 1
