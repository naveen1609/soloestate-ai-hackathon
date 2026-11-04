"""D2 dispatcher-policy corrections (independent-review HIGH findings 1-3).

Finding 1: lead-scoped cooldown/open-attempt counts. Finding 2: reservation
acquire/validate split. Finding 3 (shippable part): missing-policy fail-closed
at the transition; the enablement-control decision is documented for the owner.

Rollback by default; the two race tests commit with exact-ID cleanup plus
delivery_caps save/restore.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import crm_schemas as s
from app import dispatch, receptionist
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


@pytest.fixture
def lm(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2lm-' + str(uuid4())
            b = dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
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


def _sender():
    return '1555%07d' % (uuid4().int % 10000000)


def _inbound(b, sender, body='Fictional hello'):
    from app import identity_links
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.LIM' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        response = {'provider_ts': now.isoformat(),
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
    return dict(link=link, lead=lead, convo=convo, message=message, wamid=wamid, op_key=op_key)


def _second_message(b, box, body='Fictional follow-up'):
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        wamid = 'wamid.LIM' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        response = {'provider_ts': now.isoformat(),
                    'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': box['link']['provider_ref'],
                                         'link_id': str(box['link']['id'])}}
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', box['link']['id'], Jsonb(response)))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (box['convo']['id'],)).fetchone()['next']
        return cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                           'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                           (uuid4(), box['convo']['id'], seq, 'customer', body, op_key)).fetchone()


def _outbox(b, message_id):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE source_id=%s',
                           (str(message_id),)).fetchone()


def _welcome_turn():
    return json.dumps(dict(catalog_version=CATALOG_VERSION, category='welcome',
                           segments=[dict(phrase_id='ACK_RECEIVED', args={})]))


def _business_day(b):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        tz = cur.execute("SELECT timezone FROM soloestate.business_policies WHERE id='default'").fetchone()['timezone']
        now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    return now.astimezone(ZoneInfo(tz)).date()


def _used(b, channel='whatsapp'):
    return _used_on(b, _business_day(b), channel)


def _used_on(b, day, channel='whatsapp'):
    with b['conn'].cursor() as cur:
        row = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE day=%s AND channel=%s',
                          (day, channel)).fetchone()
        return row[0] if row else 0


def _seed_cap(b, used, cap):
    day = _business_day(b)
    with b['conn'].cursor() as cur:
        cur.execute("""INSERT INTO soloestate.delivery_caps(day,channel,used,cap) VALUES (%s,'whatsapp',%s,%s)
            ON CONFLICT (day,channel) DO UPDATE SET used=%s, cap=%s""", (day, used, cap, used, cap))
    return day


def _stage_kwargs(b, box, message):
    return dict(request_key=dispatch.stable_key('whatsapp', 'reply', str(message['id'])),
                channel='whatsapp', purpose='reply', source_id=str(message['id']),
                lead_id=box['lead']['id'], conversation_id=box['convo']['id'], link_id=box['link']['id'],
                destination='+' + box['link']['provider_ref'], category='welcome',
                content_ref='reply:' + str(message['id']), catalog_version=CATALOG_VERSION,
                payload={'body': 'Fictional reply', 'catalog_version': CATALOG_VERSION},
                fact_deps=[])


# --- Finding 1: lead-scoped cooldown ---

def test_lim01_second_row_same_lead_skips_on_open_attempt(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    assert _outbox(b, box['message']['id'])['status'] == 'staged'
    msg2 = _second_message(b, box)
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, msg2))
    assert out['created'] is True and out['record']['status'] == 'skipped'
    assert out['record']['skip_reason'] == 'cooldown_active'


def test_lim02_unknown_attempt_blocks_new_message(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim02',
                              submit=StubSubmit([dispatch.SubmitUnknown('timeout')])) == {'status': 'unknown'}
    msg2 = _second_message(b, box)
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, msg2))
    assert out['record']['status'] == 'skipped' and out['record']['skip_reason'] == 'cooldown_active'


def test_lim03_submitting_attempt_blocks_new_message(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    with b['conn'].cursor() as cur:
        cur.execute("UPDATE soloestate.delivery_attempts SET status='submitting' WHERE outbox_id=%s", (row['id'],))
    msg2 = _second_message(b, box)
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, msg2))
    assert out['record']['status'] == 'skipped' and out['record']['skip_reason'] == 'cooldown_active'


def test_lim04_recent_acknowledged_send_blocks_new_message(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim04',
                              submit=StubSubmit(['wamid.LIM04']))['status'] == 'sent'
    msg2 = _second_message(b, box)
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, msg2))
    assert out['record']['status'] == 'skipped' and out['record']['skip_reason'] == 'cooldown_active'


def test_lim06_other_lead_unaffected_by_cooldown(lm):
    b = lm
    box_a = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box_a['convo']['id'], box_a['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row_a = _outbox(b, box_a['message']['id'])
    assert dispatch.drive_row(b['factory'], row_a['id'], worker='lim06a',
                              submit=StubSubmit(['wamid.LIM06']))['status'] == 'sent'
    box_b = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box_b['convo']['id'], box_b['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row_b = _outbox(b, box_b['message']['id'])
    assert row_b['status'] == 'staged'
    assert dispatch.drive_row(b['factory'], row_b['id'], worker='lim06b',
                              submit=StubSubmit(['wamid.LIM06B']))['status'] == 'sent'


# --- Finding 2: reservation acquire/validate split ---

def test_lim10_cap1_first_reserved_message_submits(lm):
    b = lm
    _seed_cap(b, 0, 1)
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert row['status'] == 'staged' and _used(b) == 1
    submit = StubSubmit(['wamid.LIM10'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim10', submit=submit)['status'] == 'sent'
    assert len(submit.calls) == 1 and _used(b) == 1
    box2 = _inbound(b, _sender())
    result = receptionist.respond_turn(b['factory'], box2['convo']['id'], box2['message']['id'],
                                       model=StubModel([_welcome_turn()]))
    assert result['replied'] is True
    row2 = _outbox(b, box2['message']['id'])
    assert row2['status'] == 'skipped' and row2['skip_reason'] == 'cap_exhausted'
    assert _used(b) == 1


def test_lim11_cap25_final_reservation_submits(lm):
    b = lm
    _seed_cap(b, DAY_CAP_DEFAULT - 1, DAY_CAP_DEFAULT)
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert row['status'] == 'staged' and _used(b) == DAY_CAP_DEFAULT
    submit = StubSubmit(['wamid.LIM11'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim11', submit=submit)['status'] == 'sent'
    assert len(submit.calls) == 1 and _used(b) == DAY_CAP_DEFAULT


def test_lim13_transition_never_double_charges(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert _used(b) == 1
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim13',
                              submit=StubSubmit(['wamid.LIM13']))['status'] == 'sent'
    assert _used(b) == 1
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim13b',
                              submit=StubSubmit(['wamid.NEVER']))['status'] == 'sent'
    assert _used(b) == 1


def test_lim14_full_cap_skips_restage_without_bypass(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=True, reason='Fictional D2 stop'))
    driven = dispatch.drive_row(b['factory'], row['id'], worker='lim14',
                                submit=StubSubmit(['wamid.NEVER']))
    assert driven['status'] == 'blocked'
    assert _outbox(b, box['message']['id'])['status'] == 'queued' and _used(b) == 0
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=False, reason='Fictional D2 lift'))
    _seed_cap(b, DAY_CAP_DEFAULT, DAY_CAP_DEFAULT)
    redriven = dispatch.drive_row(b['factory'], row['id'], worker='lim14b',
                                  submit=StubSubmit(['wamid.NEVER2']))
    assert redriven['status'] == 'skipped' and redriven['reasons'] == ['cap_exhausted']
    assert _outbox(b, box['message']['id'])['status'] == 'skipped'
    assert _used(b) == DAY_CAP_DEFAULT


def test_lim15_requeue_refuses_when_cap_full(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim15',
                              submit=StubSubmit([dispatch.SubmitFailed('invalid')])) == {'status': 'dead'}
    assert _used(b) == 0
    _seed_cap(b, DAY_CAP_DEFAULT, DAY_CAP_DEFAULT)
    with pytest.raises(CRMError, match='exhausted'):
        dispatch.requeue(b['crm'], row['id'], dispatch.OutboxRequeue(
            request_key=b['key'](), reason='Fictional requeue under full cap'))
    assert _outbox(b, box['message']['id'])['status'] == 'dead'
    assert _used(b) == DAY_CAP_DEFAULT


# --- Finding 3 (shippable part): missing-policy fail-closed ---

def test_lim20_missing_policy_blocks_transition(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    day = _business_day(b)
    assert row['reserved_day'] == day and _used_on(b, day) == 1
    with b['conn'].cursor() as cur:
        cur.execute("DELETE FROM soloestate.business_policies WHERE id='default'")
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='lim20', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['policy_missing']
    assert submit.calls == []
    assert _outbox(b, box['message']['id'])['status'] == 'queued'
    assert _used_on(b, day) == 0


def test_lim21_stop_after_terminal_send_changes_nothing(lm):
    b = lm
    box = _inbound(b, _sender())
    receptionist.respond_turn(b['factory'], box['convo']['id'], box['message']['id'],
                              model=StubModel([_welcome_turn()]))
    row = _outbox(b, box['message']['id'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='lim21',
                              submit=StubSubmit(['wamid.LIM21']))['status'] == 'sent'
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=True, reason='Fictional D2 stop'))
    assert _outbox(b, box['message']['id'])['status'] == 'sent'
    msg2 = _second_message(b, box)
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, msg2))
    assert out['record']['status'] == 'skipped'
    assert 'emergency_stop' in out['record']['skip_reason'].split(',')
    set_stop(b['crm'], s.ControlsSet(request_key=b['key'](), stopped=False, reason='Fictional D2 lift'))


# --- Committed race tests (exact-ID cleanup + caps save/restore) ---

def _committed_lead(sender):
    from app import identity_links
    now = datetime.now(timezone.utc)
    observe_key = 'd2lim-observe:' + str(uuid4())
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            link, _ = identity_links.observe(cur, 'whatsapp', sender, observe_key)
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (observe_key, 'identity-link:observe', 'd2lim:' + observe_key, 'channel', link['id'],
                         Jsonb({'record': {'id': str(link['id'])}})))
            convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s '
                                'ORDER BY created_at DESC LIMIT 1', (link['lead_id'],)).fetchone()
            cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
            messages = []
            for _ in range(2):
                wamid = 'wamid.LIM' + uuid4().hex[:12]
                op_key = 'wa:inbound:' + wamid
                response = {'provider_ts': now.isoformat(),
                            'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                                 'link_id': str(link['id'])}}
                cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                            'VALUES (%s,%s,%s,%s,%s,%s)',
                            (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
                seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                                  'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
                messages.append(cur.execute(
                    'INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                    'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                    (uuid4(), convo['id'], seq, 'customer', 'Fictional hello', op_key)).fetchone())
    return dict(link_id=str(link['id']), lead_id=str(link['lead_id']), convo_id=str(convo['id']),
                message_ids=[str(m['id']) for m in messages],
                op_keys=[observe_key, messages[0]['request_key'], messages[1]['request_key']])


def _caps_snapshot(day, channel='whatsapp'):
    with connect(Settings()) as conn:
        return conn.execute('SELECT used, cap FROM soloestate.delivery_caps WHERE day=%s AND channel=%s',
                            (day, channel)).fetchone()


def _restore_caps(day, before, channel='whatsapp'):
    with connect(Settings()) as conn:
        if before is None:
            conn.execute('DELETE FROM soloestate.delivery_caps WHERE day=%s AND channel=%s', (day, channel))
        else:
            conn.execute('UPDATE soloestate.delivery_caps SET used=%s, cap=%s WHERE day=%s AND channel=%s',
                         (before[0], before[1], day, channel))


def _cleanup_committed(box, outbox_ids):
    with connect(Settings()) as conn:
        conn.execute('DELETE FROM soloestate.delivery_outbox WHERE id = ANY(%s::uuid[])', (outbox_ids,))
        conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id=%s', (box['convo_id'],))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (box['lead_id'],))
        conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (box['convo_id'],))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key = ANY(%s)', (box['op_keys'],))
        conn.execute('DELETE FROM soloestate.identity_links WHERE id=%s', (box['link_id'],))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (box['lead_id'],))
    with connect(Settings()) as conn:
        left = conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=%s', (box['lead_id'],)).fetchone()[0]
        assert left == 0


def _business_day_committed():
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            tz = cur.execute("SELECT timezone FROM soloestate.business_policies WHERE id='default'").fetchone()['timezone']
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    return now.astimezone(ZoneInfo(tz)).date()


def _committed_kwargs(box, sender, message_id):
    return dict(request_key=dispatch.stable_key('whatsapp', 'reply', str(message_id)),
                channel='whatsapp', purpose='reply', source_id=str(message_id),
                lead_id=box['lead_id'], conversation_id=box['convo_id'], link_id=box['link_id'],
                destination='+' + sender, category='welcome',
                content_ref='reply:' + str(message_id), catalog_version=CATALOG_VERSION,
                payload={'body': 'Fictional reply', 'catalog_version': CATALOG_VERSION},
                fact_deps=[])


def _refund_units(day, count, channel='whatsapp'):
    from app.caps import refund_cap
    with connect(Settings()) as conn:
        for _ in range(count):
            refund_cap(conn, day, channel)


def _policy_enable_committed():
    from app.policies import ROUTINE_CATEGORIES
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            before = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                 "WHERE id='whatsapp' FOR UPDATE").fetchone()
            owned = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=true, categories=%s, '
                                'version=version+1, updated_at=clock_timestamp() WHERE id=%s '
                                'RETURNING enabled, categories, version',
                                (sorted(ROUTINE_CATEGORIES), 'whatsapp')).fetchone()
    return {'before': {'enabled': before['enabled'], 'categories': list(before['categories'])},
            'owned': {'enabled': owned['enabled'], 'categories': list(owned['categories']),
                      'version': owned['version']}}


def _policy_restore_committed(state):
    """Compare-and-set restore of before-values; None, else a conflict message.

    Versions advance monotonically and are never decremented or reused; a
    lost race preserves the current row untouched.
    """
    before, owned = state['before'], state['owned']
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            won = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=%s, categories=%s, '
                              'version=version+1, updated_at=clock_timestamp() WHERE id=%s AND version=%s '
                              'AND enabled=%s AND categories=%s RETURNING version',
                              (before['enabled'], before['categories'], 'whatsapp', owned['version'],
                               owned['enabled'], owned['categories'])).fetchone()
            if won is not None:
                return None
            current = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                  "WHERE id='whatsapp'").fetchone()
    return ('immediate-reply-policy restore refused: row changed since setup '
            '(owned version %s; current version %s, enabled=%s); preserved current state'
            % (owned['version'], current['version'], current['enabled']))


def _run_race(callables):
    barrier = Barrier(len(callables))
    results, errors = {}, {}

    def runner(index, func):
        try:
            barrier.wait(timeout=60)
            results[index] = func()
        except Exception as exc:  # noqa: BLE001 - surfaced below, never swallowed
            errors[index] = exc

    threads = [Thread(target=runner, args=(i, func)) for i, func in enumerate(callables)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), 'race thread hung'
    assert not errors, errors
    return [results[i] for i in range(len(callables))]


def test_lim05_stage_race_same_lead_one_winner(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    sender = _sender()
    box = _committed_lead(sender)
    day = _business_day_committed()
    caps_before = _caps_snapshot(day)
    policy_state = _policy_enable_committed()
    crm = CRM()
    conflict = None
    try:
        outs = _run_race([
            lambda: dispatch.stage(crm, **_committed_kwargs(box, sender, box['message_ids'][0])),
            lambda: dispatch.stage(crm, **_committed_kwargs(box, sender, box['message_ids'][1])),
        ])
        states = sorted(o['record']['status'] for o in outs)
        assert states == ['skipped', 'staged']
        skipped = next(o for o in outs if o['record']['status'] == 'skipped')
        assert skipped['record']['skip_reason'] == 'cooldown_active'
        staged = next(o for o in outs if o['record']['status'] == 'staged')
        assert staged['record']['reserved'] is True
        _refund_units(day, 1)
    finally:
        with connect(Settings()) as conn:
            rows = conn.execute('SELECT id FROM soloestate.delivery_outbox WHERE lead_id=%s',
                                (box['lead_id'],)).fetchall()
        _cleanup_committed(box, [str(r[0]) for r in rows])
        _restore_caps(day, caps_before)
        conflict = _policy_restore_committed(policy_state)
    assert conflict is None, conflict
    assert _caps_snapshot(day) == caps_before


def test_lim12_final_cap_unit_race_one_winner(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    day = _business_day_committed()
    caps_before = _caps_snapshot(day)
    assert caps_before is None or caps_before[0] == 0, caps_before
    with connect(Settings()) as conn:
        conn.execute("""INSERT INTO soloestate.delivery_caps(day,channel,used,cap) VALUES (%s,'whatsapp',24,25)
            ON CONFLICT (day,channel) DO UPDATE SET used=24, cap=25""", (day,))
    sender_a, sender_b = _sender(), _sender()
    box_a, box_b = _committed_lead(sender_a), _committed_lead(sender_b)
    policy_state = _policy_enable_committed()
    crm = CRM()
    conflict = None
    try:
        outs = _run_race([
            lambda: dispatch.stage(crm, **_committed_kwargs(box_a, sender_a, box_a['message_ids'][0])),
            lambda: dispatch.stage(crm, **_committed_kwargs(box_b, sender_b, box_b['message_ids'][0])),
        ])
        states = sorted(o['record']['status'] for o in outs)
        assert states == ['skipped', 'staged']
        skipped = next(o for o in outs if o['record']['status'] == 'skipped')
        assert skipped['record']['skip_reason'] == 'cap_exhausted'
        staged = next(o for o in outs if o['record']['status'] == 'staged')
        submit = StubSubmit(['wamid.LIM12'])

        @contextmanager
        def committed_factory():
            with connect(Settings()) as conn:
                yield conn

        assert dispatch.drive_row(committed_factory, staged['record']['id'],
                                  worker='lim12', submit=submit)['status'] == 'sent'
        assert len(submit.calls) == 1
        _refund_units(day, 1)
    finally:
        for box in (box_a, box_b):
            with connect(Settings()) as conn:
                rows = conn.execute('SELECT id FROM soloestate.delivery_outbox WHERE lead_id=%s',
                                    (box['lead_id'],)).fetchall()
            _cleanup_committed(box, [str(r[0]) for r in rows])
        _restore_caps(day, caps_before)
        conflict = _policy_restore_committed(policy_state)
    assert conflict is None, conflict
    assert _caps_snapshot(day) == caps_before
