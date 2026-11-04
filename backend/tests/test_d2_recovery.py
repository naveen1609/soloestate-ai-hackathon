"""D2 webhook projection/turn transaction-boundary recovery tests.Production-path coverage: the real process_now/process_message with the realreceptionist turn over genuinely separate connections (a fresh connect() perfactory call, committed with exact-ID cleanup). Only model output and providersubmission are injected.The stub-turn tests in test_d2_database.py and test_d2_channels.py cannot seethis boundary: their fixture factory yields one shared connection, so the turnalways observes the uncommitted projection. In production the turn opens itsown connection, so an uncommitted projection is invisible and the turn returnsmessage_not_found — which the old protocol then marked processed."""
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Thread
from uuid import uuid4
import pytest
from psycopg.rows import dict_row
from app import receptionist
from app import whatsapp as wa
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM
from app.database import connect
from app.services.meta_model import ModelResult
pytestmark = pytest.mark.d2_database
class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0
    async def generate(self, prompt):
        self.calls += 1
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])
class SubmitRecorder:
    def __init__(self, wamid):
        self.wamid = wamid
        self.calls = []
    def __call__(self, recipient, text):
        self.calls.append((recipient, text))
        return self.wamid
@pytest.fixture
def rec(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    @contextmanager
    def factory():
        with connect(Settings()) as conn:
            yield conn
    prefix = 'd2rec-' + str(uuid4())
    box = dict(factory=factory, crm=CRM(), prefix=prefix,               key=lambda: prefix + ':' + str(uuid4()),               wamids=[], senders=[], provider_ids=[],               policy_state=_policy_enable_committed(),               caps_before=_caps_snapshot())
    yield box
    _cleanup(box)
def _policy_enable_committed():
    from app.policies import ROUTINE_CATEGORIES
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            before = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '                                 "WHERE id='whatsapp' FOR UPDATE").fetchone()
            owned = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=true, categories=%s, '                                'version=version+1, updated_at=clock_timestamp() WHERE id=%s '                                'RETURNING enabled, categories, version',                                (sorted(ROUTINE_CATEGORIES), 'whatsapp')).fetchone()
    return {'before': {'enabled': before['enabled'], 'categories': list(before['categories'])},            'owned': {'enabled': owned['enabled'], 'categories': list(owned['categories']),                      'version': owned['version']}}
def _policy_restore_committed(state):
    before, owned = state['before'], state['owned']
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            won = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=%s, categories=%s, '                              'version=version+1, updated_at=clock_timestamp() WHERE id=%s AND version=%s '                              'AND enabled=%s AND categories=%s RETURNING version',                              (before['enabled'], before['categories'], 'whatsapp', owned['version'],                               owned['enabled'], owned['categories'])).fetchone()
            if won is not None:
                return None
            current = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '                                  "WHERE id='whatsapp'").fetchone()
    return ('immediate-reply-policy restore refused: row changed since setup '            '(owned version %s; current version %s, enabled=%s); preserved current state'            % (owned['version'], current['version'], current['enabled']))
def _caps_snapshot():
    with connect(Settings()) as conn:
        return conn.execute("SELECT day, used, cap FROM soloestate.delivery_caps WHERE channel='whatsapp' "                            'ORDER BY day').fetchall()
def _restore_caps(before):
    with connect(Settings()) as conn:
        conn.execute("DELETE FROM soloestate.delivery_caps WHERE channel='whatsapp'")
        for row in before:
            conn.execute('INSERT INTO soloestate.delivery_caps(day, channel, used, cap) VALUES (%s, %s, %s, %s)',                         (row[0], 'whatsapp', row[1], row[2]))
def _sender(box):
    sender = '1555%07d' % (uuid4().int % 10000000)
    box['senders'].append(sender)
    return sender
def _wamid(box, tag):
    wamid = 'wamid.REC%s%s' % (tag, uuid4().hex[:10])
    box['wamids'].append(wamid)
    return wamid
def _payload(messages=(), statuses=()):
    return {'object': 'whatsapp_business_account',            'entry': [{'id': 'D2TESTACCOUNTID', 'changes': [{'value': {                'messaging_product': 'whatsapp',                'metadata': {'display_phone_number': '+15550000000', 'phone_number_id': 'D2TESTPHONEID'},                'messages': list(messages), 'statuses': list(statuses)}}]}]}
def _msg(wamid, sender, body='Fictional hello', kind='text'):
    now = int(datetime.now(timezone.utc).timestamp())
    message = {'id': wamid, 'from': sender, 'timestamp': str(now), 'type': kind}
    if kind == 'text':
        message['text'] = {'body': body}
    return message
def _ingest(box, payload):
    events = wa.extract_events(payload)
    raws = []
    for entry in payload['entry']:
        for change in entry['changes']:
            value = change['value']
            raws.extend([{'messages': [m]} for m in value.get('messages') or []])
            raws.extend([{'statuses': [st]} for st in value.get('statuses') or []])
    with box['factory']() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return wa.ingest_batch(cur, events, raws, datetime.now(timezone.utc))
def _welcome():
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category='welcome',                           segments=[dict(phrase_id='ACK_RECEIVED', args={})]))
def _op_key(wamid):
    return 'wa:inbound:' + wamid
def _rows(box):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ops = [_op_key(w) for w in box['wamids']]
            customers = cur.execute('SELECT * FROM soloestate.conversation_messages WHERE request_key = ANY(%s) '                                    'ORDER BY seq', (ops,)).fetchall()
            convos = sorted({str(m['conversation_id']) for m in customers})
            messages = []
            for convo in convos:
                messages.extend(cur.execute('SELECT * FROM soloestate.conversation_messages '                                            'WHERE conversation_id=%s ORDER BY seq', (convo,)).fetchall())
            convos = sorted({str(m['conversation_id']) for m in messages})
            customers = [m for m in messages if m['role'] == 'customer']
            replies = [m for m in messages if m['role'] == 'assistant']
            outbox = []
            for convo in convos:
                outbox.extend(cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE conversation_id=%s '                                          'ORDER BY created_at', (convo,)).fetchall())
            events = cur.execute('SELECT * FROM soloestate.webhook_events WHERE provider_msg_id = ANY(%s)',                                 (box['wamids'],)).fetchall()
            return dict(messages=messages, convos=convos, customers=customers, replies=replies,                        outbox=outbox, events=events)
def _cleanup(box):
    conflict = None
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ops = [_op_key(w) for w in box['wamids']]
            customers = cur.execute('SELECT id, conversation_id FROM soloestate.conversation_messages '                                    'WHERE request_key = ANY(%s)', (ops,)).fetchall()
            customer_ids = [str(m['id']) for m in customers]
            convos = sorted({str(m['conversation_id']) for m in customers})
            messages = cur.execute('SELECT id FROM soloestate.conversation_messages '                                   'WHERE conversation_id = ANY(%s::uuid[])', (convos,)).fetchall() if convos else []
            mids = [str(m['id']) for m in messages]
            reply_keys = ['channel:reply:' + mid for mid in customer_ids]
            ob_ids = [str(r['id']) for r in                      cur.execute('SELECT id FROM soloestate.delivery_outbox WHERE conversation_id = ANY(%s::uuid[])',                                  (convos,)).fetchall()] if convos else []
            lead_ids = [str(r['lead_id']) for r in                        cur.execute('SELECT lead_id FROM soloestate.conversations WHERE id = ANY(%s::uuid[])',                                    (convos,)).fetchall()] if convos else []
            link_ids = [str(r['id']) for r in                        cur.execute('SELECT id FROM soloestate.identity_links WHERE lead_id = ANY(%s::uuid[])',                                    (lead_ids,)).fetchall()] if lead_ids else []
        conn.execute('DELETE FROM soloestate.delivery_receipts WHERE provider_msg_id = ANY(%s)',                     (box['provider_ids'],))
        conn.execute('DELETE FROM soloestate.delivery_outbox WHERE id = ANY(%s::uuid[])', (ob_ids,))
        conn.execute('DELETE FROM soloestate.booking_requests WHERE conversation_id = ANY(%s::uuid[])', (convos,))
        conn.execute('DELETE FROM soloestate.conversation_summaries WHERE conversation_id = ANY(%s::uuid[])',                     (convos,))
        conn.execute('DELETE FROM soloestate.conversation_messages WHERE id = ANY(%s::uuid[])', (mids,))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id = ANY(%s::uuid[])', (lead_ids,))
        conn.execute('DELETE FROM soloestate.conversations WHERE id = ANY(%s::uuid[])', (convos,))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key = ANY(%s)', (ops + reply_keys,))
        conn.execute('DELETE FROM soloestate.webhook_conflicts WHERE provider_event_id = ANY(%s)',                     (['msg:' + w for w in box['wamids']],))
        conn.execute('DELETE FROM soloestate.webhook_events WHERE provider_msg_id = ANY(%s)',                     (box['wamids'] + box['provider_ids'],))
        conn.execute('DELETE FROM soloestate.identity_links WHERE id = ANY(%s::uuid[])', (link_ids,))
        conn.execute('DELETE FROM soloestate.leads WHERE id = ANY(%s::uuid[])', (lead_ids,))
    _restore_caps(box['caps_before'])
    conflict = _policy_restore_committed(box['policy_state'])
    assert conflict is None, conflict
    with connect(Settings()) as conn:
        left = conn.execute('SELECT count(*) FROM soloestate.delivery_receipts WHERE provider_msg_id = ANY(%s)',                            (box['provider_ids'],)).fetchone()[0]
        assert left == 0, left
        left = conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key = ANY(%s)',                            (['wa:inbound:' + w for w in box['wamids']],)).fetchone()[0]
        assert left == 0, left
        left = conn.execute('SELECT count(*) FROM soloestate.webhook_events WHERE provider_msg_id = ANY(%s)',                            (box['wamids'] + box['provider_ids'],)).fetchone()[0]
        assert left == 0, left
def test_rec01_first_message_unknown_sender_end_to_end(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, '01')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    submit = SubmitRecorder('wamid.RECSENT01')
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=submit)
    assert out[0]['processed'] is True
    state = _rows(rec)
    assert len(state['customers']) == 1 and len(state['replies']) == 1
    assert state['customers'][0]['body'] == 'Fictional hello'
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'
    assert state['outbox'][0]['provider_msg_id'] == 'wamid.RECSENT01'
    assert len(submit.calls) == 1
    assert len(state['events']) == 1 and state['events'][0]['status'] == 'processed'
    assert state['events'][0]['attempts'] == 0
def test_rec02_existing_sender_continues_thread_with_cooldown_skip(rec):
    sender = _sender(rec)
    first = _wamid(rec, '02a')
    assert _ingest(rec, _payload(messages=[_msg(first, sender)]))[0]['outcome'] == 'stored'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.RECSENT02a'))
    assert out[0]['processed'] is True
    second = _wamid(rec, '02b')
    assert _ingest(rec, _payload(messages=[_msg(second, sender, body='Fictional follow-up')]))[0]['outcome'] == 'stored'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.NEVER02'))
    assert out[0]['processed'] is True
    state = _rows(rec)
    assert state['convos'] and len(state['convos']) == 1
    assert len(state['customers']) == 2 and len(state['replies']) == 2
    assert [m['seq'] for m in state['customers']] == [1, 3]
    by_source = {str(row['source_id']): row for row in state['outbox']}
    assert len(by_source) == 2
    first_mid = str(state['customers'][0]['id'])
    assert by_source[first_mid]['status'] == 'sent'
    skipped = [row for key, row in by_source.items() if key != first_mid]
    assert len(skipped) == 1 and skipped[0]['status'] == 'skipped'
    assert skipped[0]['skip_reason'] == 'cooldown_active'
    assert all(e['status'] == 'processed' for e in state['events'])
def test_rec03_projection_commits_before_turn_crash(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, '03')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    def boom(*args, **kwargs):
        raise RuntimeError('Fictional turn crash')
    out = wa.process_now(rec['factory'], turn=boom)
    assert out[0]['processed'] is False and out[0]['reason'] == 'retry'
    with connect(Settings()) as conn:
        persisted = conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key=%s',                                 (_op_key(wamid),)).fetchone()[0]
        assert persisted == 1
        status = conn.execute('SELECT status, attempts FROM soloestate.webhook_events WHERE provider_event_id=%s',                              ('msg:' + wamid,)).fetchone()
        assert tuple(status) == ('received', 1)
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.RECSENT03'))
    assert out[0]['processed'] is True
    state = _rows(rec)
    assert len(state['customers']) == 1 and len(state['replies']) == 1
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'
def test_rec04_turn_commit_survives_crash_before_completion(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, '04')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    calls = []
    def flaky(factory, conversation_id, message_id, model=None):
        result = receptionist.respond_turn(factory, conversation_id, message_id, model=model)
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('Fictional crash after turn commit')
        return result
    out = wa.process_now(rec['factory'], turn=flaky, model=StubModel([_welcome()]),                         submit=SubmitRecorder('wamid.RECSENT04'))
    assert out[0]['processed'] is False and out[0]['reason'] == 'retry'
    with connect(Settings()) as conn:
        replies = conn.execute("SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id IN "                               "(SELECT conversation_id FROM soloestate.conversation_messages WHERE request_key=%s) "                               "AND role='assistant'", (_op_key(wamid),)).fetchone()[0]
        assert replies == 1
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.RECSENT04'))
    assert out[0]['processed'] is True
    state = _rows(rec)
    assert len(state['replies']) == 1
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'
def test_rec05_duplicate_webhook_replays_without_duplicates(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, '05')
    payload = _payload(messages=[_msg(wamid, sender)])
    assert _ingest(rec, payload)[0]['outcome'] == 'stored'
    assert _ingest(rec, payload)[0]['outcome'] == 'replay'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.RECSENT05'))
    assert out[0]['processed'] is True
    assert wa.process_now(rec['factory'], model=StubModel([_welcome()])) == []
    state = _rows(rec)
    assert len(state['customers']) == 1 and len(state['replies']) == 1
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'
    assert len(state['events']) == 1 and state['events'][0]['status'] == 'processed'
def test_rec06_concurrent_workers_single_reply_with_lease_recovery(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, '06')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    entered = threading.Event()
    release = threading.Event()
    def blocking_turn(factory, conversation_id, message_id, model=None):
        entered.set()
        assert release.wait(timeout=60), 'turn barrier timed out'
        return receptionist.respond_turn(factory, conversation_id, message_id, model=model)
    results, errors = {}, {}
    def runner():
        try:
            results['first'] = wa.process_now(rec['factory'], turn=blocking_turn,                                              model=StubModel([_welcome()]),                                              submit=SubmitRecorder('wamid.RECSENT06'))
        except Exception as exc:  # noqa: BLE001 - surfaced below, never swallowed
            errors['first'] = exc
    worker = Thread(target=runner)
    worker.start()
    assert entered.wait(timeout=60), 'worker never reached the turn'
    with connect(Settings()) as conn:
        for _ in range(100):
            found = conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key=%s',                                 (_op_key(wamid),)).fetchone()[0]
            if found == 1:
                break
            conn.rollback()
            time.sleep(0.2)
        assert found == 1, 'projection never committed'
        conn.execute("UPDATE soloestate.webhook_events SET lease_until=clock_timestamp()-interval '1 second' "                     'WHERE provider_event_id=%s', ('msg:' + wamid,))
        with conn.cursor(row_factory=dict_row) as cur:
            swept = wa.sweep_expired(cur, 'sweeper-test')
        assert swept == {'dead': [], 'released': ['msg:' + wamid]}
    second_submit = SubmitRecorder('wamid.RECSENT06')
    second = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=second_submit)
    assert second[0]['processed'] is True
    release.set()
    worker.join(timeout=120)
    assert not worker.is_alive(), 'worker hung'
    assert not errors, errors
    first = results['first'][0]
    assert first['processed'] is False and first['reason'] == 'not_claimed'
    assert len(second_submit.calls) == 1
    state = _rows(rec)
    assert len(state['replies']) == 1
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'
def _claim_one(box, event_id, owner):
    with box['factory']() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            got = wa.claim_events(cur, owner, limit=10)
            assert event_id in got
            return wa._claim_generation(cur, event_id)
def _event_state(box, event_id):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute('SELECT status, attempts, lease_owner, claim_generation, lease_until '                               'FROM soloestate.webhook_events WHERE provider_event_id=%s',                               (event_id,)).fetchone()
def _expire_lease(box, event_id):
    with connect(Settings()) as conn:
        conn.execute("UPDATE soloestate.webhook_events SET lease_until=clock_timestamp()-interval '1 second' "                     'WHERE provider_event_id=%s', (event_id,))
def _sweep(box):
    with box['factory']() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return wa.sweep_expired(cur, 'sweeper-test')
def test_fen01_stale_release_cannot_overwrite_completed(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, 'F01')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    event_id = 'msg:' + wamid
    gen_a = _claim_one(rec, event_id, 'worker-A')
    _expire_lease(rec, event_id)
    assert _sweep(rec) == {'dead': [], 'released': [event_id]}
    out = wa.process_now(rec['factory'], owner='worker-B', model=StubModel([_welcome()]),                         submit=SubmitRecorder('wamid.FENSENT01'))
    assert out[0]['processed'] is True
    before = _event_state(rec, event_id)
    assert before['status'] == 'processed' and before['attempts'] == 0
    assert wa._release(rec['factory'], event_id, owner='worker-A', generation=gen_a) is False
    assert _event_state(rec, event_id) == before
def test_fen02_stale_dead_cannot_overwrite_renewed_claim(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, 'F02')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    event_id = 'msg:' + wamid
    gen_a = _claim_one(rec, event_id, 'worker-A')
    _expire_lease(rec, event_id)
    assert _sweep(rec) == {'dead': [], 'released': [event_id]}
    gen_b = _claim_one(rec, event_id, 'worker-B')
    assert gen_b == gen_a + 1
    assert wa._park_dead(rec['factory'], event_id, owner='worker-A', generation=gen_a) is False
    state = _event_state(rec, event_id)
    assert state['status'] == 'claimed' and state['lease_owner'] == 'worker-B'
    assert state['attempts'] == 0 and state['claim_generation'] == gen_b
def test_fen03_stale_worker_never_projects_turns_or_releases(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, 'F03')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    event_id = 'msg:' + wamid
    gen_a = _claim_one(rec, event_id, 'worker-A')
    _expire_lease(rec, event_id)
    assert _sweep(rec) == {'dead': [], 'released': [event_id]}
    gen_b = _claim_one(rec, event_id, 'worker-B')
    out = wa.process_message(rec['factory'], event_id, owner='worker-A', generation=gen_a,                             model=StubModel([_welcome()]))
    assert out == {'processed': False, 'reason': 'not_claimed'}
    state = _event_state(rec, event_id)
    assert state['status'] == 'claimed' and state['lease_owner'] == 'worker-B' and state['attempts'] == 0
    with connect(Settings()) as conn:
        persisted = conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE request_key=%s',                                 (_op_key(wamid),)).fetchone()[0]
        assert persisted == 0
    out = wa.process_message(rec['factory'], event_id, owner='worker-B', generation=gen_b,                             model=StubModel([_welcome()]))
    assert out['processed'] is True
    state = _rows(rec)
    assert len(state['customers']) == 1 and len(state['replies']) == 1
def test_fen04_receipt_completion_obeys_fencing(rec):
    wamid = _wamid(rec, 'F04')
    assert _ingest(rec, _payload(statuses=[{'id': wamid, 'status': 'sent',                                            'timestamp': '1700000000'}]))[0]['outcome'] == 'stored'
    event_id = 'rcpt:' + wamid + ':sent'
    gen_a = _claim_one(rec, event_id, 'worker-A')
    _expire_lease(rec, event_id)
    assert _sweep(rec) == {'dead': [], 'released': [event_id]}
    gen_b = _claim_one(rec, event_id, 'worker-B')
    calls = []
    def spy(cur, *args):
        calls.append(args)
        return {'matched': True}
    out = wa.process_receipt(rec['factory'], event_id, reconcile=spy, owner='worker-A', generation=gen_a)
    assert out == {'processed': False, 'reason': 'not_claimed'} and calls == []
    state = _event_state(rec, event_id)
    assert state['status'] == 'claimed' and state['lease_owner'] == 'worker-B' and state['attempts'] == 0
    out = wa.process_receipt(rec['factory'], event_id, reconcile=spy, owner='worker-B', generation=gen_b)
    assert out['processed'] is True and len(calls) == 1
    before = _event_state(rec, event_id)
    assert before['status'] == 'processed' and before['attempts'] == 0
    assert wa._release(rec['factory'], event_id, owner='worker-A', generation=gen_a) is False
    assert _event_state(rec, event_id) == before
def test_fen05_current_owner_releases_and_parks_dead(rec):
    from app.crm import CRMError
    sender = _sender(rec)
    dead_wamid = _wamid(rec, 'F05a')
    assert _ingest(rec, _payload(messages=[_msg(dead_wamid, sender)]))[0]['outcome'] == 'stored'
    def bad4(*args, **kwargs):
        raise CRMError('Fictional terminal failure', 422)
    out = wa.process_now(rec['factory'], turn=bad4)
    assert out[0]['reason'] == 'dead'
    state = _event_state(rec, 'msg:' + dead_wamid)
    assert state['status'] == 'dead' and state['lease_owner'] is None
    retry_wamid = _wamid(rec, 'F05b')
    assert _ingest(rec, _payload(messages=[_msg(retry_wamid, sender)]))[0]['outcome'] == 'stored'
    def boom(*args, **kwargs):
        raise RuntimeError('Fictional transient failure')
    out = wa.process_now(rec['factory'], turn=boom)
    assert out[0]['reason'] == 'retry'
    state = _event_state(rec, 'msg:' + retry_wamid)
    assert state['status'] == 'received' and state['attempts'] == 1 and state['lease_owner'] is None
def test_fen06_same_worker_name_cannot_bypass_generation(rec):
    sender = _sender(rec)
    wamid = _wamid(rec, 'F06')
    assert _ingest(rec, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    event_id = 'msg:' + wamid
    gen_a = _claim_one(rec, event_id, 'worker-W')
    _expire_lease(rec, event_id)
    assert _sweep(rec) == {'dead': [], 'released': [event_id]}
    gen_b = _claim_one(rec, event_id, 'worker-W')
    assert gen_b == gen_a + 1
    assert wa._release(rec['factory'], event_id, owner='worker-W', generation=gen_a) is False
    assert wa._park_dead(rec['factory'], event_id, owner='worker-W', generation=gen_a) is False
    state = _event_state(rec, event_id)
    assert state['status'] == 'claimed' and state['lease_owner'] == 'worker-W'
    assert state['attempts'] == 0 and state['claim_generation'] == gen_b
    out = wa.process_message(rec['factory'], event_id, owner='worker-W', generation=gen_b,                             model=StubModel([_welcome()]))
    assert out['processed'] is True
def _stage_and_row(box, tag):
    sender = _sender(box)
    wamid = _wamid(box, tag)
    assert _ingest(box, _payload(messages=[_msg(wamid, sender)]))[0]['outcome'] == 'stored'
    out = wa.process_now(box['factory'], model=StubModel([_welcome()]))
    assert out[0]['processed'] is True
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            mid = cur.execute('SELECT id FROM soloestate.conversation_messages WHERE request_key=%s',                              (_op_key(wamid),)).fetchone()['id']
            row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',                              (str(mid),)).fetchone()
            assert row is not None and row['status'] == 'staged'
    return sender, wamid, str(row['id'])
class StubSubmit:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
    def __call__(self, recipient, text):
        self.calls.append((recipient, text))
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
def _ingest_receipt(box, provider_id, status_key):
    box['provider_ids'].append(provider_id)
    payload = _payload(statuses=[{'id': provider_id, 'status': status_key, 'timestamp': '1700000000'}])
    assert _ingest(box, payload)[0]['outcome'] in ('stored', 'replay')
def _process_receipts(box):
    out = wa.process_now(box['factory'])
    assert out and all(item['processed'] for item in out), out
    return out
def _pending(box, provider_id):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute('SELECT * FROM soloestate.delivery_receipts WHERE provider_msg_id=%s',                               (provider_id,)).fetchall()
def _outbox_row(box, outbox_id):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s', (outbox_id,)).fetchone()
def _attempt_row(box, outbox_id):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE outbox_id=%s '                               'ORDER BY attempt_no DESC LIMIT 1', (outbox_id,)).fetchone()
def _used_for(box, outbox_id):
    with connect(Settings()) as conn:
        day = conn.execute('SELECT reserved_day FROM soloestate.delivery_outbox WHERE id=%s',                           (outbox_id,)).fetchone()[0]
        return conn.execute("SELECT used FROM soloestate.delivery_caps WHERE channel='whatsapp' AND day=%s",                            (day,)).fetchone()[0]
def test_rcv01_delivered_read_receipts_before_ack_drain_on_ack(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R01')
    pid = 'wamid.RCV01'
    _ingest_receipt(rec, pid, 'delivered')
    _process_receipts(rec)
    _ingest_receipt(rec, pid, 'read')
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'staged'
    driven = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv1', submit=SubmitRecorder(pid))
    assert driven == {'status': 'sent', 'provider_msg_id': pid}
    assert _outbox_row(rec, outbox_id)['status'] == 'read'
    pending = _pending(rec, pid)
    assert len(pending) == 2 and all(p['applied_outcome'] is not None for p in pending)
    assert _used_for(rec, outbox_id) == 1
def test_rcv02_failed_receipt_before_ack_refunds_exactly_once(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R02')
    pid = 'wamid.RCV02'
    assert _used_for(rec, outbox_id) == 1
    _ingest_receipt(rec, pid, 'failed')
    _process_receipts(rec)
    driven = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv2', submit=SubmitRecorder(pid))
    assert driven == {'status': 'sent', 'provider_msg_id': pid}
    assert _outbox_row(rec, outbox_id)['status'] == 'failed'
    assert _outbox_row(rec, outbox_id)['reserved'] is False
    assert _used_for(rec, outbox_id) == 0
    # Idempotency: a duplicate failed receipt (replayed webhook + a direct
    # reconcile) never refunds twice.
    _ingest_receipt(rec, pid, 'failed')
    assert wa.process_now(rec['factory']) == []
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            assert dispatch.reconcile_receipt(cur, pid, 'failed', None, {}) == {                'matched': True, 'duplicate': True}
    assert _used_for(rec, outbox_id) == 0
    assert _outbox_row(rec, outbox_id)['status'] == 'failed'
def test_rcv03_ack_before_receipt_advances_normally(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R03')
    pid = 'wamid.RCV03'
    driven = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv3', submit=SubmitRecorder(pid))
    assert driven == {'status': 'sent', 'provider_msg_id': pid}
    _ingest_receipt(rec, pid, 'delivered')
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'delivered'
    _ingest_receipt(rec, pid, 'read')
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'read'
    assert _used_for(rec, outbox_id) == 1
def test_rcv04a_receipt_while_submission_in_flight(rec):
    from app import dispatch
    from threading import Event
    sender, wamid, outbox_id = _stage_and_row(rec, 'R04a')
    pid = 'wamid.RCV04a'
    entered, finish, results = Event(), Event(), {}
    def blocking_submit(recipient, text):
        entered.set()
        assert finish.wait(timeout=60), 'submit barrier timed out'
        return pid
    def run_drive():
        results['drive'] = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv4a', submit=blocking_submit)
    worker = Thread(target=run_drive)
    worker.start()
    assert entered.wait(timeout=60), 'submit never started'
    # The receipt commits while the submission is in flight (attempt has no
    # provider id yet): retained as pending, never resubmitted.
    _ingest_receipt(rec, pid, 'delivered')
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'submitting'
    finish.set()
    worker.join(timeout=60)
    assert not worker.is_alive(), 'drive hung'
    assert results['drive'] == {'status': 'sent', 'provider_msg_id': pid}
    assert _outbox_row(rec, outbox_id)['status'] == 'delivered'
    pending = _pending(rec, pid)
    assert len(pending) == 1 and pending[0]['applied_outcome'] is not None
def test_rcv04b_ack_and_receipt_contend_for_receipt_lock(rec):
    from app import dispatch
    from threading import Event
    sender, wamid, outbox_id = _stage_and_row(rec, 'R04b')
    pid = 'wamid.RCV04b'
    _ingest_receipt(rec, pid, 'delivered')
    drive_started, receipt_started, results = Event(), Event(), {}
    def run_drive():
        drive_started.set()
        results['drive'] = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv4b',                                              submit=SubmitRecorder(pid))
    def run_receipt():
        receipt_started.set()
        results['receipt'] = wa.process_now(rec['factory'])
    with connect(Settings()) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",                     ('receipt:whatsapp:' + pid,))
        first = Thread(target=run_drive)
        second = Thread(target=run_receipt)
        first.start()
        second.start()
        assert drive_started.wait(timeout=60) and receipt_started.wait(timeout=60)
        conn.commit()
    first.join(timeout=60)
    second.join(timeout=60)
    assert not first.is_alive() and not second.is_alive()
    assert results['drive'] == {'status': 'sent', 'provider_msg_id': pid}
    assert results['receipt'][0]['processed'] is True
    assert _outbox_row(rec, outbox_id)['status'] == 'delivered'
    assert len(_attempts_list(rec, outbox_id)) == 1
    assert _used_for(rec, outbox_id) == 1
def _attempts_list(box, outbox_id):
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE outbox_id=%s',                               (outbox_id,)).fetchall()
def test_rcv05_duplicate_receipts_stay_idempotent(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R05')
    pid = 'wamid.RCV05'
    payload = _payload(statuses=[{'id': pid, 'status': 'delivered', 'timestamp': '1700000000'}])
    rec['provider_ids'].append(pid)
    assert _ingest(rec, payload)[0]['outcome'] == 'stored'
    assert _ingest(rec, payload)[0]['outcome'] == 'replay'
    _process_receipts(rec)
    assert wa.process_now(rec['factory']) == []
    driven = dispatch.drive_row(rec['factory'], outbox_id, worker='rcv5', submit=SubmitRecorder(pid))
    assert driven == {'status': 'sent', 'provider_msg_id': pid}
    assert _outbox_row(rec, outbox_id)['status'] == 'delivered'
    pending = _pending(rec, pid)
    assert len(pending) == 1 and pending[0]['applied_outcome'] is not None
    assert len(_attempts_list(rec, outbox_id)) == 1
    assert _used_for(rec, outbox_id) == 1
def test_rcv06_unknown_without_known_id_stays_unresolved(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R06')
    submit = StubSubmit([dispatch.SubmitUnknown('timeout')])
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='rcv6', submit=submit) == {'status': 'unknown'}
    rec['provider_ids'].append('wamid.UNRELATED')
    payload = _payload(statuses=[{'id': 'wamid.UNRELATED', 'status': 'delivered',                                  'timestamp': '1700000000'}])
    assert _ingest(rec, payload)[0]['outcome'] == 'stored'
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'unknown'
    attempt = _attempt_row(rec, outbox_id)
    assert attempt['status'] == 'unknown' and attempt['provider_msg_id'] is None
    pending = _pending(rec, 'wamid.UNRELATED')
    assert len(pending) == 1 and pending[0]['applied_outcome'] is None
    assert len(submit.calls) == 1  # never auto-resent
def test_rcv07_late_receipt_cannot_overwrite_owner_resolution(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'R07')
    pid = 'wamid.RCV07'
    submit = StubSubmit([dispatch.SubmitUnknown('timeout', pid)])
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='rcv7', submit=submit) == {'status': 'unknown'}
    assert _used_for(rec, outbox_id) == 1
    resolved = dispatch.resolve_unknown(rec['crm'], outbox_id, dispatch.OutboxResolve(        request_key=rec['key'](), action='mark_resolved_sent',        reason='Fictional portal verification', evidence='Fictional provider portal check'))
    assert resolved['record']['status'] == 'sent'
    _ingest_receipt(rec, pid, 'failed')
    _process_receipts(rec)
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'sent'
    attempt = _attempt_row(rec, outbox_id)
    assert (attempt['outcome'] or '').startswith('owner_resolved_sent')
    assert _used_for(rec, outbox_id) == 1  # no receipt refund over owner evidence
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='rcv7b', submit=submit) == {'status': 'sent'}
    assert len(submit.calls) == 1  # terminal: never resubmitted
def _hold_outbox(box, outbox_id):
    """Manual lock-holder connection (committed factory, held open)."""
    conn = connect(Settings())
    conn.execute('SELECT id FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE', (outbox_id,))
    return conn
def _lock_wait_baseline():
    with connect(Settings()) as conn:
        return conn.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "                            "AND backend_type='client backend'").fetchone()[0]
def _wait_lock_blocked(baseline, minimum, timeout=90):
    # One persistent monitor connection: a fresh pooler connect per poll
    # makes the observation cadence slower than the 5s lock_timeout
    # contention windows this helper exists to observe.
    deadline = time.time() + timeout
    with connect(Settings()) as conn:
        while time.time() < deadline:
            waiting = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' "
                                   "AND backend_type='client backend'").fetchone()[0]
            if waiting >= baseline + minimum:
                return True
            time.sleep(0.2)
    return False
def _nowait_free(sql, params, conn=None):
    own = conn is None
    if own:
        conn = connect(Settings())
    try:
        conn.execute(sql + ' FOR UPDATE NOWAIT', params)
        conn.rollback()
        return True
    except Exception:  # noqa: BLE001 - lock held elsewhere is the signal
        conn.rollback()
        return False
    finally:
        if own:
            conn.close()
@contextmanager
def _borrowed(conn):
    """Yield a pre-opened connection as one transaction (test factory)."""
    with conn:
        yield conn


def _claim_receipt_event(box, provider_id, status_key):
    """Claim this test's receipt event in the main thread (production claim
    path) so the worker reaches the outbox lock within the 5s lock_timeout."""
    from psycopg.rows import dict_row
    with box['factory']() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("SELECT provider_event_id FROM soloestate.webhook_events "
                              "WHERE provider_msg_id=%s AND event_kind='receipt' AND status_key=%s",
                              (provider_id, status_key)).fetchone()
            assert row is not None, 'receipt event not stored'
            event_id = row['provider_event_id']
            claimed = wa.claim_events(cur, 'lok-claim', limit=10)
            assert event_id in claimed, claimed
            generation = wa._claim_generation(cur, event_id)
    return event_id, generation


def _spawn(fn):
    outcome = {}
    def run():
        try:
            outcome['result'] = fn()
        except Exception as exc:  # noqa: BLE001 - surfaced below, never swallowed
            outcome['error'] = exc
    worker = Thread(target=run)
    worker.start()
    return worker, outcome
def _join(box_threads, label):
    for worker, outcome in box_threads:
        worker.join(timeout=120)
        assert not worker.is_alive(), '%s hung' % label
        assert 'error' not in outcome, (label, outcome.get('error'))
def test_lok01_receipt_vs_owner_resolve_sent(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'L01')
    pid = 'wamid.LOK01'
    submit = StubSubmit([dispatch.SubmitUnknown('timeout', pid)])
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='lok1', submit=submit) == {'status': 'unknown'}
    rec['provider_ids'].append(pid)
    _ingest_receipt(rec, pid, 'failed')
    event_id, generation = _claim_receipt_event(rec, pid, 'failed')
    attempt_id = str(_attempt_row(rec, outbox_id)['id'])
    probe_conn = connect(Settings())
    receipt_conn = connect(Settings())
    holder = _hold_outbox(rec, outbox_id)
    baseline = _lock_wait_baseline()
    resolve_worker, resolve_outcome = _spawn(lambda: dispatch.resolve_unknown(        rec['crm'], outbox_id, dispatch.OutboxResolve(            request_key=rec['key'](), action='mark_resolved_sent',            reason='Fictional portal verification', evidence='Fictional provider portal check')))
    contended_resolve = _wait_lock_blocked(baseline, 1, timeout=30)
    receipt_worker, receipt_outcome = _spawn(lambda: wa.process_receipt(
        lambda: _borrowed(receipt_conn), event_id, owner='lok-claim', generation=generation))
    contended_receipt = _wait_lock_blocked(baseline, 2, timeout=4)
    # Protocol discriminator: queued behind the outbox, NEITHER contender may
    # already hold the attempt row (the old attempt-first order would).
    attempt_free = _nowait_free('SELECT id FROM soloestate.delivery_attempts WHERE id=%s', (attempt_id,), probe_conn)
    holder.rollback()
    holder.close()
    probe_conn.close()
    assert contended_resolve, 'resolve never contended'
    assert contended_receipt, 'receipt never contended'
    assert attempt_free, 'a contender held the attempt before the outbox'
    _join([(resolve_worker, resolve_outcome), (receipt_worker, receipt_outcome)], 'lok01')
    receipt_conn.close()
    assert resolve_outcome['result']['record']['status'] == 'sent'
    assert receipt_outcome['result']['processed'] is True
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'sent'
    attempt = _attempt_row(rec, outbox_id)
    assert (attempt['outcome'] or '').startswith('owner_resolved_sent')
    assert _used_for(rec, outbox_id) == 1  # receipt never refunded over owner evidence
    pending = _pending(rec, pid)
    assert len(pending) == 1 and pending[0]['applied_outcome'] == {'matched': True, 'stale': True}
def test_lok02_receipt_vs_owner_abandon(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'L02')
    pid = 'wamid.LOK02'
    submit = StubSubmit([dispatch.SubmitUnknown('timeout', pid)])
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='lok2', submit=submit) == {'status': 'unknown'}
    assert _used_for(rec, outbox_id) == 1
    rec['provider_ids'].append(pid)
    _ingest_receipt(rec, pid, 'failed')
    event_id, generation = _claim_receipt_event(rec, pid, 'failed')
    attempt_id = str(_attempt_row(rec, outbox_id)['id'])
    probe_conn = connect(Settings())
    receipt_conn = connect(Settings())
    holder = _hold_outbox(rec, outbox_id)
    baseline = _lock_wait_baseline()
    abandon_worker, abandon_outcome = _spawn(lambda: dispatch.resolve_unknown(        rec['crm'], outbox_id, dispatch.OutboxResolve(            request_key=rec['key'](), action='abandon',            reason='Fictional give up', evidence='')))
    contended_abandon = _wait_lock_blocked(baseline, 1, timeout=30)
    receipt_worker, receipt_outcome = _spawn(lambda: wa.process_receipt(
        lambda: _borrowed(receipt_conn), event_id, owner='lok-claim', generation=generation))
    contended_receipt = _wait_lock_blocked(baseline, 2, timeout=4)
    attempt_free = _nowait_free('SELECT id FROM soloestate.delivery_attempts WHERE id=%s', (attempt_id,), probe_conn)
    holder.rollback()
    holder.close()
    probe_conn.close()
    assert contended_abandon, 'abandon never contended'
    assert contended_receipt, 'receipt never contended'
    assert attempt_free, 'a contender held the attempt before the outbox'
    _join([(abandon_worker, abandon_outcome), (receipt_worker, receipt_outcome)], 'lok02')
    receipt_conn.close()
    assert abandon_outcome['result']['record']['status'] == 'skipped'
    assert receipt_outcome['result']['processed'] is True
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'skipped' and row['reserved'] is False
    attempt = _attempt_row(rec, outbox_id)
    assert attempt['status'] == 'superseded'
    assert (attempt['outcome'] or '').startswith('abandoned')
    assert _used_for(rec, outbox_id) == 0  # exactly one refund (the abandon)
    pending = _pending(rec, pid)
    assert len(pending) == 1 and pending[0]['applied_outcome'] == {'matched': True, 'stale': True}
def test_lok03_receipt_vs_expired_submit_sweeper(rec):
    from app import dispatch
    from threading import Event
    sender, wamid, outbox_id = _stage_and_row(rec, 'L03')
    pid = 'wamid.LOK03'
    entered, finish, results = Event(), Event(), {}
    def blocking_submit(recipient, text):
        entered.set()
        assert finish.wait(timeout=60), 'submit barrier timed out'
        return pid
    def run_drive():
        results['drive'] = dispatch.drive_row(rec['factory'], outbox_id, worker='lok3', submit=blocking_submit)
    drive_worker = Thread(target=run_drive)
    drive_worker.start()
    assert entered.wait(timeout=60), 'submit never started'
    with connect(Settings()) as conn:
        conn.execute("UPDATE soloestate.delivery_attempts SET lease_until=clock_timestamp()-interval '1 second' "
                     'WHERE outbox_id=%s', (outbox_id,))
    holder = connect(Settings())
    holder.execute('SELECT id FROM soloestate.delivery_attempts WHERE outbox_id=%s FOR UPDATE', (outbox_id,))
    sweep_worker, sweep_outcome = _spawn(lambda: dispatch.sweep(rec['factory'], 'lok3'))
    # The sweep queues behind the held attempt row lock and proceeds once it
    # is released; the drive is still blocked in submit throughout.
    holder.rollback()
    holder.close()
    _join([(sweep_worker, sweep_outcome)], 'lok03-sweep')
    assert results.get('drive') is None  # still blocked in submit
    _ingest_receipt(rec, pid, 'delivered')
    _process_receipts(rec)
    finish.set()
    drive_worker.join(timeout=60)
    assert not drive_worker.is_alive()
    assert results['drive'] == {'status': 'resolved_elsewhere', 'provider_msg_id': pid}
    assert sweep_outcome['result']['unknown'] == [str(_attempt_row(rec, outbox_id)['id'])]
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'unknown' and row['provider_msg_id'] == pid
    pending = _pending(rec, pid)
    assert len(pending) == 1 and pending[0]['applied_outcome'] is None  # retained, owner-mediated
def test_lok04_ack_vs_sweeper_then_owner_resolution(rec):
    from app import dispatch
    from threading import Event
    sender, wamid, outbox_id = _stage_and_row(rec, 'L04')
    pid = 'wamid.LOK04'
    entered, finish, results = Event(), Event(), {}
    def blocking_submit(recipient, text):
        entered.set()
        assert finish.wait(timeout=60), 'submit barrier timed out'
        return pid
    # Pre-open the drive connection: every contender's lock wait dies at
    # the 5s lock_timeout, so pooler connect latency must not eat the
    # acknowledgement's budget while the sweeper is already queued.
    # (conn.transaction(), not `with conn:` -- the latter closes the
    # connection, and drive_row opens one factory scope per phase.)
    drive_conn = connect(Settings())

    @contextmanager
    def drive_factory():
        with drive_conn.transaction():
            yield drive_conn
    drive_worker, drive_outcome = _spawn(lambda: dispatch.drive_row(
        drive_factory, outbox_id, worker='lok4', submit=blocking_submit))
    assert entered.wait(timeout=60), 'submit never started'
    with connect(Settings()) as conn:
        conn.execute("UPDATE soloestate.delivery_attempts SET lease_until=clock_timestamp()-interval '1 second' "
                     'WHERE outbox_id=%s', (outbox_id,))
    holder = _hold_outbox(rec, outbox_id)
    baseline = _lock_wait_baseline()
    sweep_worker, sweep_outcome = _spawn(lambda: dispatch.sweep(rec['factory'], 'lok4'))
    contended_sweep = _wait_lock_blocked(baseline, 1, timeout=30)
    finish.set()
    contended_ack = _wait_lock_blocked(baseline, 2, timeout=30)
    holder.rollback()
    holder.close()
    assert contended_sweep, 'sweep never contended'
    assert contended_ack, 'ack never contended'
    _join([(sweep_worker, sweep_outcome), (drive_worker, drive_outcome)], 'lok04')
    drive_conn.close()
    attempt_id = str(_attempt_row(rec, outbox_id)['id'])
    assert sweep_outcome['result']['unknown'] == [attempt_id]
    assert drive_outcome['result'] == {'status': 'resolved_elsewhere', 'provider_msg_id': pid}
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'unknown' and row['provider_msg_id'] == pid
    resolved = dispatch.resolve_unknown(rec['crm'], outbox_id, dispatch.OutboxResolve(        request_key=rec['key'](), action='mark_resolved_sent',        reason='Fictional portal verification', evidence='Fictional provider portal check'))
    assert resolved['record']['status'] == 'sent'
    assert _used_for(rec, outbox_id) == 1
def test_lok05_late_ack_after_owner_decision_moves_nothing(rec):
    from app import dispatch
    sender, wamid, outbox_id = _stage_and_row(rec, 'L05')
    pid = 'wamid.LOK05'
    submit = StubSubmit([dispatch.SubmitUnknown('timeout', pid)])
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='lok5', submit=submit) == {'status': 'unknown'}
    resolved = dispatch.resolve_unknown(rec['crm'], outbox_id, dispatch.OutboxResolve(        request_key=rec['key'](), action='mark_resolved_sent',        reason='Fictional portal verification', evidence='Fictional provider portal check'))
    assert resolved['record']['status'] == 'sent'
    # A late drive attempt is terminal-only: no resubmission, no overwrite.
    assert dispatch.drive_row(rec['factory'], outbox_id, worker='lok5b', submit=submit) == {'status': 'sent'}
    assert len(submit.calls) == 1
    # Receipts never move an owner-resolved row (documented freeze): both the
    # delivered and the failed receipt are retained as stale evidence only.
    _ingest_receipt(rec, pid, 'delivered')
    _process_receipts(rec)
    assert _outbox_row(rec, outbox_id)['status'] == 'sent'
    _ingest_receipt(rec, pid, 'failed')
    _process_receipts(rec)
    row = _outbox_row(rec, outbox_id)
    assert row['status'] == 'sent' and row['reserved'] is True
    pending = _pending(rec, pid)
    assert len(pending) == 2
    assert all(p['applied_outcome'] == {'matched': True, 'stale': True} for p in pending)
    attempt = _attempt_row(rec, outbox_id)
    assert (attempt['outcome'] or '').startswith('owner_resolved_sent')
    assert _used_for(rec, outbox_id) == 1
def test_rec07_terminal_frozen_and_unsupported_outcomes(rec):
    frozen_sender = _sender(rec)
    frozen_first = _wamid(rec, '07a')
    assert _ingest(rec, _payload(messages=[_msg(frozen_first, frozen_sender)]))[0]['outcome'] == 'stored'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]),                         submit=SubmitRecorder('wamid.RECSENT07a'))
    assert out[0]['processed'] is True
    with connect(Settings()) as conn:
        lead_id = conn.execute('SELECT lead_id FROM soloestate.conversations WHERE id IN '                               '(SELECT conversation_id FROM soloestate.conversation_messages WHERE request_key=%s)',                               (_op_key(frozen_first),)).fetchone()[0]
        conn.execute('UPDATE soloestate.leads SET archived_at=clock_timestamp() WHERE id=%s', (lead_id,))
    frozen_second = _wamid(rec, '07b')
    assert _ingest(rec, _payload(messages=[_msg(frozen_second, frozen_sender, body='Fictional after archive')]))[0]['outcome'] == 'stored'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.NEVER07'))
    assert out[0]['processed'] is True
    media_sender = _sender(rec)
    media = _wamid(rec, '07c')
    assert _ingest(rec, _payload(messages=[_msg(media, media_sender, kind='image')]))[0]['outcome'] == 'stored'
    out = wa.process_now(rec['factory'], model=StubModel([_welcome()]), submit=SubmitRecorder('wamid.NEVER07c'))
    assert out[0]['processed'] is True
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            frozen_msgs = cur.execute('SELECT role FROM soloestate.conversation_messages WHERE conversation_id IN '                                      '(SELECT conversation_id FROM soloestate.conversation_messages '                                      'WHERE request_key = ANY(%s)) ORDER BY seq',                                      ([_op_key(frozen_first), _op_key(frozen_second)],)).fetchall()
            assert [m['role'] for m in frozen_msgs] == ['customer', 'assistant', 'customer']
            media_msg = cur.execute('SELECT body FROM soloestate.conversation_messages WHERE request_key=%s',                                    (_op_key(media),)).fetchone()
            assert media_msg['body'].startswith('[Unsupported WhatsApp image')
    state = _rows(rec)
    assert all(e['status'] == 'processed' for e in state['events'])
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'sent'