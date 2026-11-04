"""D2 browser-test coordinates and exact-ownership cleanup. Fictional data only.

Cleanup deletes ONLY explicitly recorded test-owned IDs and operation rows
whose recorded response references owned outbox IDs. Shared delivery-cap
counters are decremented by exactly the owned reservations still held at
cleanup time, never reset. No date-range or action-plus-timestamp deletes.
The shared immediate-reply-policy row is restored only through an atomic
compare-and-set against the fixture-owned state (versions advance
monotonically, never backward); an intervening change is preserved and
reported as an explicit conflict after owned rows are removed.
"""
import json
import sys
from datetime import datetime, timezone
from uuid import uuid4

from app import crm_schemas as s
from app import dispatch, identity_links
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM
from app.database import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def fresh():
    return connect(Settings())


def _policy_enable_committed():
    """Lock, capture, and enable the policy; record before-values plus owned state.

    Uses raw SQL (not update_reply_policy) so no operation/audit rows are
    created. The row lock is held only inside this short transaction, never
    across browser work. Returns JSON-safe before-values and the exact
    fixture-owned post-update state that cleanup must compare-and-set against.
    """
    from app.policies import ROUTINE_CATEGORIES
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            before = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                 "WHERE id='whatsapp' FOR UPDATE").fetchone()
            owned = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=true, categories=%s, '
                                'version=version+1, updated_at=clock_timestamp() WHERE id=%s '
                                'RETURNING enabled, categories, version',
                                (sorted(ROUTINE_CATEGORIES), 'whatsapp')).fetchone()
            conn.commit()
    return {'before': {'enabled': before['enabled'], 'categories': list(before['categories'])},
            'owned': {'enabled': owned['enabled'], 'categories': list(owned['categories']),
                      'version': owned['version']}}


def _policy_restore_committed(state):
    """Restore before-values only if the row still matches the owned state.

    Single-statement compare-and-set: versions advance monotonically and are
    never decremented or reused. Returns None on success or an explicit
    conflict message when another writer intervened; a conflict preserves the
    current row untouched.
    """
    before, owned = state['before'], state['owned']
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            won = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=%s, categories=%s, '
                              'version=version+1, updated_at=clock_timestamp() WHERE id=%s AND version=%s '
                              'AND enabled=%s AND categories=%s RETURNING version',
                              (before['enabled'], before['categories'], 'whatsapp', owned['version'],
                               owned['enabled'], owned['categories'])).fetchone()
            conn.commit()
            if won is not None:
                return None
            current = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                  "WHERE id='whatsapp'").fetchone()
    return ('immediate-reply-policy restore refused: row changed since fixture setup '
            '(owned version %s; current version %s, enabled=%s); preserved current state'
            % (owned['version'], current['version'], current['enabled']))


def _unknown(*_args, **_kwargs):
    raise dispatch.SubmitUnknown('Fictional e2e timeout')


def _failed(*_args, **_kwargs):
    raise dispatch.SubmitFailed('Fictional e2e rejection')


def plan():
    from app.services.meta_model import ModelResult

    prefix = 'BrowserD2-' + str(uuid4())

    class StubModel:
        async def generate(self, prompt):
            return ModelResult(response_id='resp-e2e', model='test-meta', text=json.dumps(dict(
                catalog_version=CATALOG_VERSION, category='welcome',
                segments=[dict(phrase_id='ACK_RECEIVED', args={})])))

    crm = CRM()
    # The immediate-reply policy gates staging and resend/requeue: without an
    # enabled policy the turns below skip and the journey cannot proceed.
    # Saved first so a mid-plan failure still attempts a compare-and-set
    # restore of the prior values; a conflict there preserves the other
    # writer and is chained onto the original failure.
    policy_state = _policy_enable_committed()
    # One lead per message: the lead-scoped cooldown (§11) refuses a second
    # open/acked send for the same lead, so a shared lead cannot produce
    # three independent rows. The UI journey is lead-agnostic.
    data = dict(prefix=prefix, senders=[], lead_ids=[], link_ids=[], convo_ids=[],
                message_ids=[], outbox_ids=[], op_keys=[],
                policy_state=policy_state)
    try:
        _plan_rows(data, crm, StubModel)
    except BaseException as exc:
        conflict = _policy_restore_committed(policy_state)
        if conflict is not None:
            raise RuntimeError(conflict) from exc
        raise
    return data


def _plan_rows(data, crm, StubModel):
    from app import receptionist
    for index in range(3):
        sender = '1555%07d' % (uuid4().int % 10000000)
        data['senders'].append(sender)
        # Use the production receive() entry so the observe activity's deferred
        # FK parent op row exists; direct observe() + commit violates it.
        observe_key = 'e2e-d2-observe-' + str(uuid4())
        received = identity_links.receive(crm, 'whatsapp', sender, observe_key)
        lead_id, link_id = received['lead_id'], received['record']['id']
        convo_id = received['conversation_id']
        data['lead_ids'].append(str(lead_id))
        data['link_ids'].append(str(link_id))
        data['convo_ids'].append(str(convo_id))
        data['op_keys'].append(observe_key)
        with connect(Settings()) as conn:
            conn.execute(
                "UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s",
                (convo_id,))
        with connect(Settings()) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                wamid = 'wamid.E2E%s%d' % (uuid4().hex[:8], index)
                op_key = 'wa:inbound:' + wamid
                data['op_keys'].append(op_key)
                cur.execute(
                    'INSERT INTO soloestate.crm_operations'
                    '(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel',
                     link_id, Jsonb({
                         'provider_ts': datetime.now(timezone.utc).isoformat(),
                         'inbound_evidence': {
                             'channel': 'whatsapp', 'provider_ref': sender,
                             'link_id': str(link_id)}})))
                seq = cur.execute(
                    'SELECT coalesce(max(seq),0)+1 AS next '
                    'FROM soloestate.conversation_messages WHERE conversation_id=%s',
                    (convo_id,)).fetchone()['next']
                message = cur.execute(
                    'INSERT INTO soloestate.conversation_messages'
                    '(id,conversation_id,seq,role,body,request_key) '
                    'VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                    (uuid4(), convo_id, seq, 'customer',
                     'Fictional e2e hello %d' % index, op_key)).fetchone()
                data['message_ids'].append(str(message['id']))
                data['op_keys'].append('channel:reply:' + str(message['id']))
        result = receptionist.respond_turn(
            fresh, convo_id, message['id'], model=StubModel())
        assert result['replied'] is True, result
        data['outbox_ids'].append(result['delivery']['outbox_id'])
    for position, outbox_id in enumerate(data['outbox_ids']):
        submit = _failed if position == 2 else _unknown
        dispatch.drive_row(fresh, outbox_id, worker='e2e-d2', submit=submit)
    return data


def cleanup(data, _before_restore=None):
    """Remove fixture-owned rows and compare-and-set the shared policy row.

    _before_restore is a test-only hook invoked after owned-row cleanup and
    before policy restoration, letting a regression interleave a committed
    owner change on another connection. Production callers leave it None.
    Owned rows are always removed; a lost policy race preserves the current
    row and is reported in conflicts.
    """
    if isinstance(data, str):
        data = json.loads(data)
    outbox_ids = list(data.get('outbox_ids', []))
    conflicts = []
    with connect(Settings()) as conn:
        with conn.transaction():
            held = conn.execute(
                'SELECT reserved_day, channel, count(*) FROM soloestate.delivery_outbox '
                'WHERE id = ANY(%s::uuid[]) AND reserved GROUP BY reserved_day, channel',
                (outbox_ids,)).fetchall()
            for day, channel, count in held:
                conn.execute(
                    'UPDATE soloestate.delivery_caps SET used = used - %s '
                    'WHERE day=%s AND channel=%s', (count, day, channel))
            conn.execute(
                'DELETE FROM soloestate.delivery_attempts WHERE outbox_id = ANY(%s::uuid[])',
                (outbox_ids,))
            conn.execute(
                'DELETE FROM soloestate.delivery_outbox WHERE id = ANY(%s::uuid[])',
                (outbox_ids,))
            conn.execute(
                'DELETE FROM soloestate.conversation_summaries WHERE conversation_id = ANY(%s::uuid[])',
                (data['convo_ids'],))
            conn.execute(
                'DELETE FROM soloestate.conversation_messages WHERE conversation_id = ANY(%s::uuid[])',
                (data['convo_ids'],))
            conn.execute(
                'DELETE FROM soloestate.conversations WHERE id = ANY(%s::uuid[])',
                (data['convo_ids'],))
            conn.execute(
                'DELETE FROM soloestate.lead_activities WHERE lead_id = ANY(%s::uuid[])',
                (data['lead_ids'],))
            conn.execute(
                "DELETE FROM soloestate.crm_operations WHERE action IN "
                "('outbox:resolve','outbox:requeue') AND response->'record'->>'id' = ANY(%s)",
                (outbox_ids,))
            conn.execute(
                'DELETE FROM soloestate.crm_operations WHERE request_key = ANY(%s)',
                (data.get('op_keys', []),))
            conn.execute(
                'DELETE FROM soloestate.identity_links WHERE id = ANY(%s::uuid[])',
                (data['link_ids'],))
            conn.execute(
                'DELETE FROM soloestate.leads WHERE id = ANY(%s::uuid[])', (data['lead_ids'],))
    if data.get('policy_state') is not None:
        if _before_restore is not None:
            _before_restore()
        conflict = _policy_restore_committed(data['policy_state'])
        if conflict is not None:
            conflicts.append(conflict)
    return {'cleaned': True, 'outbox_ids': outbox_ids, 'conflicts': conflicts}


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'plan':
        print(json.dumps(plan()))
    elif command == 'cleanup':
        result = cleanup(sys.argv[2])
        print(json.dumps(result))
        if result['conflicts']:
            sys.exit(1)
    else:
        raise SystemExit('Unknown command: ' + command)
