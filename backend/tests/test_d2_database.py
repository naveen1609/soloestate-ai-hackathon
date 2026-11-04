"""D2 channel/delivery/receptionist tests. Rollback by default; committed only with exact-ID cleanup."""
import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg.rows import dict_row

from app import crm_schemas as s
from app import whatsapp as wa
from app.config import Settings
from app.crm import CRM, CRMError, scalar
from app.database import connect

pytestmark = pytest.mark.d2_database

PHONE_ID = 'D2TESTPHONEID'
ACCOUNT_ID = 'D2TESTACCOUNTID'


@pytest.fixture
def d2(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            key = lambda: 'd2-test:' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, key=key, factory=factory)


def _payload(messages=(), statuses=()):
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': ACCOUNT_ID, 'changes': [{'value': {
                'messaging_product': 'whatsapp',
                'metadata': {'display_phone_number': '+15550000000', 'phone_number_id': PHONE_ID},
                'messages': list(messages), 'statuses': list(statuses)}}]}]}


def _msg(wamid, sender, body='Fictional hello', ts=1762070400, kind='text'):
    message = {'id': wamid, 'from': sender, 'timestamp': str(ts), 'type': kind}
    if kind == 'text':
        message['text'] = {'body': body}
    return message


def _receipt(wamid, status='delivered', ts=1762070500):
    return {'id': wamid, 'status': status, 'timestamp': str(ts), 'recipient_id': '15550001111'}


def _ingest(b, payload):
    events = wa.extract_events(payload)
    raws = []
    for entry in payload['entry']:
        for change in entry['changes']:
            value = change['value']
            raws.extend([{'messages': [m]} for m in value.get('messages') or []])
            raws.extend([{'statuses': [s]} for s in value.get('statuses') or []])
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return wa.ingest_batch(cur, events, raws, datetime.now(timezone.utc))


def test_web01_valid_batch_persists_events(d2):
    out = _ingest(d2, _payload(messages=[_msg('wamid.WEB01A', '15550001111')], statuses=[_receipt('wamid.OUT1')]))
    assert [o['outcome'] for o in out] == ['stored', 'stored']
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.webhook_events WHERE provider_event_id=%s',
                          ('msg:wamid.WEB01A',)).fetchone()
        assert row['status'] == 'received' and row['event_kind'] == 'message'
        assert row['payload']['messages'][0]['id'] == 'wamid.WEB01A'
        rcpt = cur.execute('SELECT * FROM soloestate.webhook_events WHERE provider_event_id=%s',
                           ('rcpt:wamid.OUT1:delivered',)).fetchone()
        assert rcpt['status_key'] == 'delivered'


def test_web02_signature_and_identity_rules():
    secret = 'D2-fictional-secret'
    raw = b'{"entry":[]}'
    good = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert wa.verify_signature(raw, good, secret) is True
    assert wa.verify_signature(raw, good, 'wrong') is False
    assert wa.verify_signature(raw, None, secret) is False
    assert wa.verify_signature(b'x' * (wa.MAX_BODY_BYTES + 1), good, secret) is False
    from app import whatsapp_api as wapi
    from types import SimpleNamespace
    config = SimpleNamespace(whatsapp_business_account_id=ACCOUNT_ID, whatsapp_phone_number_id=PHONE_ID)
    wapi._check_identity(_payload(), config)
    with pytest.raises(CRMError, match='account'):
        wapi._check_identity({'entry': [{'id': 'OTHER', 'changes': []}]}, config)
    bad_phone = _payload()
    bad_phone['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'] = 'OTHER'
    with pytest.raises(CRMError, match='phone'):
        wapi._check_identity(bad_phone, config)
    with pytest.raises(CRMError, match='not configured'):
        wapi._check_identity(_payload(), SimpleNamespace(
            whatsapp_business_account_id=None, whatsapp_phone_number_id=None))
    with pytest.raises(CRMError):
        wa.extract_events({'entry': []})
    with pytest.raises(CRMError):
        wa.extract_events({'entry': [{'id': 'x'}] * 60})


def test_web03_duplicate_same_hash_replays(d2):
    payload = _payload(messages=[_msg('wamid.WEB03', '15550001112')])
    assert _ingest(d2, payload)[0]['outcome'] == 'stored'
    assert _ingest(d2, payload)[0]['outcome'] == 'replay'
    with d2['conn'].cursor() as cur:
        assert scalar(cur.execute("SELECT count(*) FROM soloestate.webhook_events WHERE provider_msg_id=%s",
                                  ('wamid.WEB03',)).fetchone()) == 1


def test_web04_conflict_quarantines_original_untouched(d2):
    first = _payload(messages=[_msg('wamid.WEB04', '15550001113', body='First')])
    assert _ingest(d2, first)[0]['outcome'] == 'stored'
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        before = cur.execute('SELECT payload_hash, status FROM soloestate.webhook_events '
                             'WHERE provider_event_id=%s', ('msg:wamid.WEB04',)).fetchone()
    second = _payload(messages=[_msg('wamid.WEB04', '15550001113', body='Second')])
    assert _ingest(d2, second)[0]['outcome'] == 'conflict'
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        after = cur.execute('SELECT payload_hash, status FROM soloestate.webhook_events '
                            'WHERE provider_event_id=%s', ('msg:wamid.WEB04',)).fetchone()
        assert dict(after) == dict(before)
        kept = cur.execute('SELECT count(*) AS n FROM soloestate.webhook_conflicts WHERE provider_event_id=%s',
                           ('msg:wamid.WEB04',)).fetchone()['n']
        assert kept == 1
    for n in range(12):
        altered = _payload(messages=[_msg('wamid.WEB04', '15550001113', body='Variant %d' % n)])
        assert _ingest(d2, altered)[0]['outcome'] == 'conflict'
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        kept = cur.execute('SELECT count(*) AS n FROM soloestate.webhook_conflicts WHERE provider_event_id=%s',
                           ('msg:wamid.WEB04',)).fetchone()['n']
        counter = cur.execute('SELECT conflict_count FROM soloestate.webhook_events WHERE provider_event_id=%s',
                              ('msg:wamid.WEB04',)).fetchone()['conflict_count']
        assert kept == 10 and counter == 3


def test_web05_delayed_event_processes_normally(d2):
    calls = []
    payload = _payload(messages=[_msg('wamid.WEB05', '15550001114', ts=1700000000)])
    assert _ingest(d2, payload)[0]['outcome'] == 'stored'
    out = wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    assert out[0]['processed'] is True and len(calls) == 1
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT event_time, status FROM soloestate.webhook_events WHERE provider_event_id=%s',
                          ('msg:wamid.WEB05',)).fetchone()
        assert row['status'] == 'processed'
        assert row['event_time'] == datetime.fromtimestamp(1700000000, tz=timezone.utc)


def test_web06_claim_is_single_winner_and_sweep_recovers(d2):
    _ingest(d2, _payload(messages=[_msg('wamid.WEB06', '15550001115')]))
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        first = wa.claim_events(cur, 'owner-a', limit=10)
        second = wa.claim_events(cur, 'owner-b', limit=10)
        assert first == ['msg:wamid.WEB06'] and second == []
        cur.execute("UPDATE soloestate.webhook_events SET lease_until=clock_timestamp()-interval '1 second' "
                    'WHERE provider_event_id=%s', ('msg:wamid.WEB06',))
        swept = wa.sweep_expired(cur, 'sweeper')
        assert swept == {'dead': [], 'released': ['msg:wamid.WEB06']}
        cur.execute("UPDATE soloestate.webhook_events SET status='claimed', attempts=3, "
                    "lease_until=clock_timestamp()-interval '1 second' WHERE provider_event_id=%s",
                    ('msg:wamid.WEB06',))
        swept = wa.sweep_expired(cur, 'sweeper')
        assert swept == {'dead': ['msg:wamid.WEB06'], 'released': []}


def test_web07_crash_retries_then_parks_dead(d2):
    _ingest(d2, _payload(messages=[_msg('wamid.WEB07', '15550001116')]))
    def boom(*a, **k):
        raise RuntimeError('Fictional crash')
    assert wa.process_now(d2['factory'], turn=boom)[0]['reason'] == 'retry'
    assert wa.process_now(d2['factory'], turn=boom)[0]['reason'] == 'retry'
    assert wa.process_now(d2['factory'], turn=boom)[0]['reason'] == 'retry'
    with d2['conn'].cursor() as cur:
        row = cur.execute('SELECT status, attempts FROM soloestate.webhook_events WHERE provider_event_id=%s',
                          ('msg:wamid.WEB07',)).fetchone()
        assert tuple(row) == ('dead', 3)


def test_web08_original_recovers_after_conflict(d2):
    calls = []
    _ingest(d2, _payload(messages=[_msg('wamid.WEB08', '15550001117', body='Original')]))
    _ingest(d2, _payload(messages=[_msg('wamid.WEB08', '15550001117', body='Forged')]))
    out = wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    assert out[0]['processed'] is True
    with d2['conn'].cursor() as cur:
        body = cur.execute('SELECT body FROM soloestate.conversation_messages WHERE request_key=%s',
                           ('wa:inbound:wamid.WEB08',)).fetchone()[0]
        assert body == 'Original'


def test_id01_new_sender_isolated_with_evidence(d2):
    calls = []
    _ingest(d2, _payload(messages=[_msg('wamid.ID01', '15550002221')]))
    wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        link = cur.execute('SELECT * FROM soloestate.identity_links WHERE channel=%s AND provider_ref=%s',
                           ('whatsapp', '15550002221')).fetchone()
        assert link['status'] == 'observed'
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
        assert lead['archived_at'] is None
        op = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                         ('wa:inbound:wamid.ID01',)).fetchone()['response']
        assert op['inbound_evidence'] == {'channel': 'whatsapp', 'provider_ref': '15550002221',
                                          'link_id': str(link['id'])}
        assert op['provider_ts'] == datetime.fromtimestamp(1762070400, tz=timezone.utc).isoformat()
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s', (lead['id'],)).fetchone()
        assert convo['channel'] == 'whatsapp'
    _ingest(d2, _payload(messages=[_msg('wamid.ID01B', '15550002221', body='Again')]))
    wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    with d2['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.identity_links WHERE channel=%s AND provider_ref=%s',
                                  ('whatsapp', '15550002221')).fetchone()) == 1
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversations WHERE lead_id=%s',
                                  (lead['id'],)).fetchone()) == 1


def test_id02_matching_phone_never_attaches(d2):
    from uuid import UUID
    from app.journey import JourneyUpdate, update as journey_update
    lead = d2['crm'].save('leads', s.LeadCreate(request_key=d2['key'](), name='D2 canonical',
                                                email='d2canonical@example.com'))['record']
    journey_update(d2['crm'], UUID(lead['id']),
                   JourneyUpdate(request_key=d2['key'](), expected_version=lead['version'],
                                 phone='+15550002222', reason='Fictional D2 canonical phone'))
    lead = d2['crm'].detail('leads', lead['id'])['record']
    calls = []
    _ingest(d2, _payload(messages=[_msg('wamid.ID02', '15550002222')]))
    wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        link = cur.execute('SELECT * FROM soloestate.identity_links WHERE channel=%s AND provider_ref=%s',
                           ('whatsapp', '15550002222')).fetchone()
        assert link['lead_id'] != lead['id']
        after = cur.execute('SELECT version, archived_at FROM soloestate.leads WHERE id=%s',
                            (lead['id'],)).fetchone()
        assert after['version'] == lead['version'] and after['archived_at'] is None
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.identity_links WHERE lead_id=%s',
                                  (lead['id'],)).fetchone()) == 0
        visible = scalar(cur.execute('SELECT count(*) FROM soloestate.conversations WHERE lead_id=%s',
                                     (lead['id'],)).fetchone())
        assert visible == 0


def test_unsupported_media_persists_without_reply(d2):
    calls = []
    _ingest(d2, _payload(messages=[_msg('wamid.UNSUP', '15550002223', kind='image')]))
    out = wa.process_now(d2['factory'], turn=lambda *a, **k: calls.append(a) or {'staged': []})
    assert out[0]['processed'] is True and calls == []
    with d2['conn'].cursor(row_factory=dict_row) as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key=%s',
                                  ('wa:inbound:wamid.UNSUP',)).fetchone()) == 1


def test_malformed_events_reject_without_partial_writes(d2):
    out = _ingest(d2, _payload(messages=[{'id': 'wamid.BAD', 'from': 'not-digits', 'timestamp': '1',
                                                       'type': 'text', 'text': {'body': 'x'}}]))
    assert out[0]['outcome'] == 'rejected'
    with d2['conn'].cursor() as cur:
        assert scalar(cur.execute("SELECT count(*) FROM soloestate.webhook_events WHERE status='rejected'").fetchone()) == 1
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.identity_links WHERE provider_ref=%s',
                                  ('not-digits',)).fetchone()) == 0


def test_web09_first_sight_inbound_commit_satisfies_observe_fk(request):
    # Committed by design: lead_activities.request_key is a deferred FK, so the
    # rollback suites never check it. The projection commit itself is the assert.
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    tag = uuid4().hex[:8]
    wamid = 'wamid.D2COMMIT' + tag
    sender = '1555%07d' % (uuid4().int % 10000000)
    op_key = 'wa:inbound:' + wamid
    now = datetime.now(timezone.utc)
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            projected = wa.project_inbound(
                cur, wamid=wamid, sender=sender, msg_type='text', body='Fictional hello',
                event_time=now, received_at=now)
            link_id, convo_id, msg_id = projected['link_id'], projected['conversation_id'], projected['message_id']
            lead_id = cur.execute('SELECT lead_id FROM soloestate.identity_links WHERE id=%s',
                                  (link_id,)).fetchone()['lead_id']
    try:
        with connect(Settings()) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                assert cur.execute('SELECT status FROM soloestate.identity_links WHERE id=%s',
                                   (link_id,)).fetchone()['status'] == 'observed'
                assert scalar(cur.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                          (op_key,)).fetchone()) == 1
                assert scalar(cur.execute('SELECT count(*) FROM soloestate.lead_activities WHERE lead_id=%s '
                                          'AND request_key=%s', (lead_id, op_key)).fetchone()) == 1
                assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE id=%s',
                                          (msg_id,)).fetchone()) == 1
    finally:
        with connect(Settings()) as conn:
            conn.execute('DELETE FROM soloestate.conversation_messages WHERE id=%s', (msg_id,))
            conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (lead_id,))
            conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (convo_id,))
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (op_key,))
            conn.execute('DELETE FROM soloestate.identity_links WHERE id=%s', (link_id,))
            conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (lead_id,))
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=%s', (lead_id,)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                       (op_key,)).fetchone()) == 0
