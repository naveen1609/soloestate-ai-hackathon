"""D2 identity/channel remainder (ID/WWW/WEB matrix gaps). Rollback by default."""
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from psycopg.rows import dict_row

from app import chat_schemas as cs
from app import crm_schemas as s
from app import dispatch, receptionist
from app import whatsapp as wa
from app.catalog import CATALOG_VERSION
from app.chat import resume, scope, start
from app.config import Settings
from app.crm import CRM, CRMError, scalar
from app.database import connect
from app.services.meta_model import ModelResult
from app.visitor_tokens import verify

pytestmark = pytest.mark.d2_database

PHONE_ID = 'D2TESTPHONEID'
ACCOUNT_ID = 'D2TESTACCOUNTID'


class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


@pytest.fixture
def ch(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            settings = Settings(visitor_token_secret=secrets.token_urlsafe(32))
            prefix = 'd2ch-' + str(uuid4())
            b = dict(crm=CRM(factory), conn=conn, factory=factory, settings=settings, prefix=prefix,
                     key=lambda: prefix + ':' + str(uuid4()))
            _enable(b)
            yield b


def _enable(b, categories=None):
    from app.policies import ROUTINE_CATEGORIES, update_reply_policy
    with b['conn'].cursor() as cur:
        version = cur.execute("SELECT version FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()[0]
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=version, enabled=True,
        categories=list(categories if categories is not None else ROUTINE_CATEGORIES),
        reason='Fictional D2 test enable'))


def _payload(messages=(), statuses=()):
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': ACCOUNT_ID, 'changes': [{'value': {
                'messaging_product': 'whatsapp',
                'metadata': {'display_phone_number': '+15550000000', 'phone_number_id': PHONE_ID},
                'messages': list(messages), 'statuses': list(statuses)}}]}]}


def _msg(wamid, sender, body='Fictional hello', ts=None, kind='text'):
    now = int(datetime.now(timezone.utc).timestamp())
    stamp = str(ts if ts is not None else now)
    message = {'id': wamid, 'from': sender, 'timestamp': stamp, 'type': kind}
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
            raws.extend([{'statuses': [st]} for st in value.get('statuses') or []])
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return wa.ingest_batch(cur, events, raws, datetime.now(timezone.utc))


def _canonical(b, sender):
    from uuid import UUID as _UUID
    from app.journey import JourneyUpdate, update as journey_update
    lead = b['crm'].save('leads', s.LeadCreate(request_key=b['key'](), name=b['prefix'] + '-canon',
                                               email=b['prefix'] + '-canon@example.invalid'))['record']
    journey_update(b['crm'], _UUID(lead['id']),
                   JourneyUpdate(request_key=b['key'](), expected_version=lead['version'],
                                 phone='+' + sender, whatsapp_permission='opted_in',
                                 reason='Fictional D2 canonical opt-in'))
    return b['crm'].detail('leads', lead['id'])['record']


def _welcome():
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category='welcome',
                           segments=[dict(phrase_id='ACK_RECEIVED', args={})]))


def test_id03_linked_continuity_uses_canonical_thread(ch):
    from app import identity_links
    sender = '15550004111'
    canonical = _canonical(ch, sender)
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        observed, _ = identity_links.observe(cur, 'whatsapp', sender, ch['key']())
    linked = identity_links.link(ch['crm'], s.IdentityLinkCreate(
        request_key=ch['key'](), link_id=observed['id'], lead_id=canonical['id'],
        verified_destination='+' + sender, reason='Fictional D2 link'))
    assert linked['record']['status'] == 'active'
    _ingest(ch, _payload(messages=[_msg('wamid.ID03', sender, body='Hello again')]))
    out = wa.process_now(ch['factory'], turn=lambda *a, **k: {'staged': []})
    assert out[0]['processed'] is True
    assert out[0]['projection']['conversation_id'] == linked['canonical_conversation_id']
    assert out[0]['projection']['link_id'] == str(linked['record']['id'])
    stub = StubModel([_welcome()])
    result = receptionist.respond_turn(ch['factory'], out[0]['projection']['conversation_id'],
                                       out[0]['projection']['message_id'], model=stub)
    assert result['replied'] is True and result['delivery']['status'] == 'staged'
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE conversation_id=%s',
                          (out[0]['projection']['conversation_id'],)).fetchone()
        assert row['destination_basis'] == 'link' and row['destination'] == '+' + sender
        assert str(row['identity_link_id']) == linked['record']['id']


def test_id04_revoked_inbound_mints_fresh_isolation(ch):
    from app import identity_links
    sender = '15550004112'
    canonical = _canonical(ch, sender)
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        observed, _ = identity_links.observe(cur, 'whatsapp', sender, ch['key']())
    linked = identity_links.link(ch['crm'], s.IdentityLinkCreate(
        request_key=ch['key'](), link_id=observed['id'], lead_id=canonical['id'],
        verified_destination='+' + sender, reason='Fictional D2 link'))
    identity_links.revoke(ch['crm'], linked['record']['id'], s.IdentityRevoke(
        request_key=ch['key'](), expected_version=linked['record']['version'], reason='Fictional D2 revoke'))
    _ingest(ch, _payload(messages=[_msg('wamid.ID04', sender, body='Hello after revoke')]))
    out = wa.process_now(ch['factory'], turn=lambda *a, **k: {'staged': []})
    assert out[0]['processed'] is True
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s',
                            (out[0]['projection']['link_id'],)).fetchone()
        assert fresh['status'] == 'observed' and fresh['lead_id'] != canonical['id']
        assert fresh['lead_id'] != observed['lead_id']
        old = cur.execute('SELECT status FROM soloestate.identity_links WHERE id=%s',
                          (linked['record']['id'],)).fetchone()
        assert old['status'] == 'revoked'
        convo = cur.execute('SELECT lead_id FROM soloestate.conversations WHERE id=%s',
                            (out[0]['projection']['conversation_id'],)).fetchone()
        assert convo['lead_id'] == fresh['lead_id']


def test_id06_revoke_with_queued_row_skips_link_invalid(ch):
    from app import identity_links
    sender = '15550004113'
    canonical = _canonical(ch, sender)
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        observed, _ = identity_links.observe(cur, 'whatsapp', sender, ch['key']())
    linked = identity_links.link(ch['crm'], s.IdentityLinkCreate(
        request_key=ch['key'](), link_id=observed['id'], lead_id=canonical['id'],
        verified_destination='+' + sender, reason='Fictional D2 link'))
    _ingest(ch, _payload(messages=[_msg('wamid.ID06', sender, body='Hello')]))
    out = wa.process_now(ch['factory'], turn=lambda *a, **k: {'staged': []})
    receptionist.respond_turn(ch['factory'], out[0]['projection']['conversation_id'],
                              out[0]['projection']['message_id'], model=StubModel([_welcome()]))
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE conversation_id=%s',
                          (out[0]['projection']['conversation_id'],)).fetchone()
        assert row['status'] == 'staged' and row['destination_basis'] == 'link'
    identity_links.revoke(ch['crm'], linked['record']['id'], s.IdentityRevoke(
        request_key=ch['key'](), expected_version=linked['record']['version'], reason='Fictional D2 revoke'))
    submit = lambda _recipient, _text: 'wamid.NEVER'  # noqa: E731
    assert dispatch.drive_row(ch['factory'], row['id'], worker='id6', submit=submit)['status'] == 'blocked'
    skipped = dispatch.drive_row(ch['factory'], row['id'], worker='id6b', submit=submit)
    assert skipped['status'] == 'skipped'
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        final = cur.execute('SELECT status, skip_reason FROM soloestate.delivery_outbox WHERE id=%s',
                            (row['id'],)).fetchone()
        assert (final['status'], final['skip_reason']) == ('skipped', 'skipped_link_invalid')
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_attempts WHERE outbox_id=%s',
                                  (row['id'],)).fetchone()) == 1


def _snapshot_thread(conn, convo_id):
    with conn.cursor(row_factory=dict_row) as cur:
        convo = cur.execute('SELECT id, lead_id, status, handoff_state, property_id FROM soloestate.conversations '
                            'WHERE id=%s', (convo_id,)).fetchone()
        messages = cur.execute('SELECT id, seq, role, body, grounding, citations FROM soloestate.conversation_messages '
                               'WHERE conversation_id=%s ORDER BY seq', (convo_id,)).fetchall()
    return dict(convo=dict(convo), messages=[dict(m) for m in messages])


def test_www05_link_cycles_keep_visitor_thread_identical(ch):
    from app import identity_links
    from app.conversation import append
    from app.crm_schemas import MessageAppend
    begun = start(ch['crm'], cs.ChatStart(request_key=ch['key'](), name=ch['prefix'],
                                         email=ch['prefix'] + '@example.invalid'), ch['settings'])
    convo_id = UUID(begun['conversation']['id'])
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        version = cur.execute('SELECT version FROM soloestate.conversations WHERE id=%s', (convo_id,)).fetchone()['version']
    append(ch['crm'], convo_id, MessageAppend(request_key=ch['key'](), expected_version=version,
                                               role='customer', body='Fictional visitor question',
                                               supersedes_message_id=None),
           actor='visitor', visitor_send=True)
    before = _snapshot_thread(ch['conn'], convo_id)
    sender = '15550004114'
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        observed, _ = identity_links.observe(cur, 'whatsapp', sender, ch['key']())
    linked = identity_links.link(ch['crm'], s.IdentityLinkCreate(
        request_key=ch['key'](), link_id=observed['id'], lead_id=before['convo']['lead_id'],
        verified_destination='+' + sender, reason='Fictional D2 link into chat lead'))
    assert _snapshot_thread(ch['conn'], convo_id) == before
    identity_links.revoke(ch['crm'], linked['record']['id'], s.IdentityRevoke(
        request_key=ch['key'](), expected_version=linked['record']['version'], reason='Fictional D2 revoke'))
    assert _snapshot_thread(ch['conn'], convo_id) == before


def test_www_scope_token_post_link(ch):
    from app import identity_links
    begun = start(ch['crm'], cs.ChatStart(request_key=ch['key'](), name=ch['prefix'],
                                         email=ch['prefix'] + '@example.invalid'), ch['settings'])
    token, isolated = begun['token'], UUID(begun['conversation']['id'])
    assert verify(token, ch['settings']) == isolated
    sender = '15550004115'
    canonical = _canonical(ch, sender)
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        observed, _ = identity_links.observe(cur, 'whatsapp', sender, ch['key']())
        canonical_convo = cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) '
                                      'VALUES (%s,%s,%s) RETURNING id',
                                      (uuid4(), canonical['id'], None)).fetchone()['id']
    identity_links.link(ch['crm'], s.IdentityLinkCreate(
        request_key=ch['key'](), link_id=observed['id'], lead_id=canonical['id'],
        verified_destination='+' + sender, reason='Fictional D2 link'))
    assert verify(token, ch['settings']) == isolated
    thread = resume(ch['crm'], isolated)
    assert str(thread['conversation']['id']) == str(isolated)
    with pytest.raises(CRMError, match='authorize'):
        scope(isolated, canonical_convo)


def test_web03_receipt_identity_three_distinct_events(ch):
    sender = '15550004116'
    box = _inbound_like(ch, sender)
    receptionist.respond_turn(ch['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome()]))
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',
                          (str(box['message']['id']),)).fetchone()
    driven = dispatch.drive_row(ch['factory'], row['id'], worker='web03',
                                submit=lambda _r, _t: 'wamid.WEB03R')
    assert driven['status'] == 'sent'
    out = _ingest(ch, _payload(statuses=[_receipt('wamid.WEB03R', 'sent'), _receipt('wamid.WEB03R', 'delivered'),
                                         _receipt('wamid.WEB03R', 'read')]))
    assert [o['outcome'] for o in out] == ['stored', 'stored', 'stored']
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        ids = cur.execute("SELECT provider_event_id FROM soloestate.webhook_events WHERE provider_msg_id=%s "
                          'ORDER BY provider_event_id', ('wamid.WEB03R',)).fetchall()
        assert [r['provider_event_id'] for r in ids] == ['rcpt:wamid.WEB03R:delivered', 'rcpt:wamid.WEB03R:read',
                                                         'rcpt:wamid.WEB03R:sent']
    results = wa.process_now(ch['factory'])
    assert [r['processed'] for r in results] == [True, True, True]
    with ch['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                                  (row['id'],)).fetchone()) == 'read'


def _inbound_like(b, sender):
    from app import identity_links
    from psycopg.types.json import Jsonb
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.CH' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb({
                        'provider_ts': now.isoformat(),
                        'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                             'link_id': str(link['id'])}})))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
        message = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), convo['id'], seq, 'customer', 'Fictional hello', op_key)).fetchone()
        cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (convo['id'],)).fetchone()
    return dict(link=link, lead=lead, convo=convo, message=message)


def test_web05_multi_entry_crash_then_recovers(ch):
    _ingest(ch, _payload(messages=[_msg('wamid.WEB05A', '15550004117', body='First'),
                                   _msg('wamid.WEB05B', '15550004118', body='Second')]))
    calls = []

    def flaky(factory, conversation_id, message_id, model=None):
        calls.append(message_id)
        if len(calls) == 1:
            raise RuntimeError('Fictional crash')
        return {'staged': []}

    first = wa.process_now(ch['factory'], turn=flaky)
    assert sorted(r.get('reason', 'processed') for r in first) == ['processed', 'retry']
    second = wa.process_now(ch['factory'], turn=flaky)
    assert [r['processed'] for r in second] == [True]
    with ch['conn'].cursor() as cur:
        for wamid in ('wamid.WEB05A', 'wamid.WEB05B'):
            assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key=%s',
                                      ('wa:inbound:' + wamid,)).fetchone()) == 1
            assert scalar(cur.execute("SELECT status FROM soloestate.webhook_events WHERE provider_event_id=%s",
                                      ('msg:' + wamid,)).fetchone()) == 'processed'


def _api(b, authed=True):
    from fastapi.testclient import TestClient

    from app.crm_api import service
    from app.main import create_app
    from app.owner_api import require_owner

    app = create_app()
    if authed:
        app.dependency_overrides[require_owner] = lambda: 'owner'
    app.dependency_overrides[service] = lambda: b['crm']
    return TestClient(app)


def test_outbox_routes_require_owner(ch):
    with _api(ch, authed=False) as api:
        assert api.get('/owner/crm/outbox').status_code == 401
        assert api.post('/owner/crm/outbox/%s/resolve' % uuid4(),
                        json={'request_key': 'x', 'action': 'abandon', 'reason': 'x'}).status_code == 401
        assert api.post('/owner/crm/outbox/%s/requeue' % uuid4(),
                        json={'request_key': 'x', 'reason': 'x'}).status_code == 401


def test_outbox_routes_list_resolve_requeue(ch):
    box = _inbound_like(ch, '15550004119')
    receptionist.respond_turn(ch['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome()]))
    with ch['conn'].cursor(row_factory=dict_row) as cur:
        row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',
                          (str(box['message']['id']),)).fetchone()
    dispatch.drive_row(ch['factory'], row['id'], worker='route',
                       submit=_failing_submit())
    with _api(ch) as api:
        listing = api.get('/owner/crm/outbox', params={'status': 'unknown'})
        assert listing.status_code == 200
        assert str(row['id']) in {item['id'] for item in listing.json()['items']}
        abandoned = api.post('/owner/crm/outbox/%s/resolve' % row['id'],
                             json={'request_key': ch['key'](), 'action': 'abandon',
                                   'reason': 'Fictional route abandon'})
        assert abandoned.status_code == 200
        assert abandoned.json()['record']['status'] == 'skipped'
        requeued = api.post('/owner/crm/outbox/%s/requeue' % row['id'],
                            json={'request_key': ch['key'](), 'reason': 'Fictional route requeue'})
        assert requeued.status_code == 200
        assert requeued.json()['record']['status'] == 'staged'


def _failing_submit():
    def submit(_recipient, _text):
        raise dispatch.SubmitUnknown('Fictional route timeout')

    return submit
