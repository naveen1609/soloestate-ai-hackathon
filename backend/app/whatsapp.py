"""WhatsApp Cloud API thin channel adapter: webhook ingest, identity projection, durable processing.

Inbound WhatsApp messages are normalized into the shared conversation engine:
identity resolves through D1 observe/link rules (never attaching unverified
senders to existing leads), messages persist with server-stamped channel
evidence, and the shared receptionist turn produces catalog-validated replies
staged through the durable outbox. Outbound submission lives in app/dispatch.py.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.crm import CRMError

MAX_BODY_BYTES = 256 * 1024
MAX_EVENTS_PER_PAYLOAD = 50
LEASE_SECONDS = 300
MAX_ATTEMPTS = 3

SUPPORTED_INBOUND_TYPES = ('text',)


def _settings():
    from app.config import Settings
    return Settings()


def _digits(sender):
    return isinstance(sender, str) and sender.isdigit() and 6 <= len(sender) <= 16


def verify_signature(raw: bytes, signature: str | None, app_secret: str | None) -> bool:
    if not raw or not signature or not app_secret:
        return False
    if len(raw) > MAX_BODY_BYTES:
        return False
    expected = 'sha256=' + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _event_time(value):
    try:
        moment = int(value)
    except (TypeError, ValueError):
        return None
    if moment < 0:
        return None
    return datetime.fromtimestamp(moment, tz=timezone.utc)


def _provider_key(kind, wamid, status_key=None):
    if kind == 'message':
        return 'msg:' + wamid
    return 'rcpt:' + wamid + ':' + (status_key or 'sent')


def extract_events(payload):
    """Normalize a validated Cloud API payload into event dicts.

    Raises CRMError(400) on envelope violations. Per-message defects are
    returned as rejected-event dicts so the batch still persists cleanly.
    """
    if not isinstance(payload, dict):
        raise CRMError('Webhook payload must be an object.', 400)
    entries = payload.get('entry')
    if not isinstance(entries, list) or not entries:
        raise CRMError('Webhook payload has no entries.', 400)
    events = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CRMError('Webhook entry must be an object.', 400)
        changes = entry.get('changes')
        if not isinstance(changes, list):
            raise CRMError('Webhook entry has no changes.', 400)
        for change in changes:
            if not isinstance(change, dict):
                raise CRMError('Webhook change must be an object.', 400)
            value = change.get('value')
            if not isinstance(value, dict):
                raise CRMError('Webhook change has no value.', 400)
            events.extend(_extract_value(value))
    if len(events) > MAX_EVENTS_PER_PAYLOAD:
        raise CRMError('Webhook payload carries too many events.', 400)
    return events


def _extract_value(value):
    events = []
    metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
    for message in value.get('messages') or []:
        events.append(_extract_message(message, metadata))
    for status in value.get('statuses') or []:
        events.append(_extract_receipt(status, metadata))
    return events


def _extract_message(message, metadata):
    if not isinstance(message, dict):
        return {'rejected': True, 'reason': 'message entry must be an object'}
    wamid = message.get('id')
    sender = message.get('from')
    if not isinstance(wamid, str) or not wamid or len(wamid) > 100:
        return {'rejected': True, 'reason': 'message id missing or oversized'}
    if not _digits(sender):
        return {'rejected': True, 'reason': 'sender identity missing or malformed',
                'provider_msg_id': wamid}
    kind = message.get('type')
    body = ''
    if kind == 'text':
        text = message.get('text')
        if not isinstance(text, dict) or not isinstance(text.get('body'), str):
            return {'rejected': True, 'reason': 'text message has no body', 'provider_msg_id': wamid}
        body = text['body']
        if not body.strip() or len(body) > 4096:
            return {'rejected': True, 'reason': 'text body empty or oversized', 'provider_msg_id': wamid}
    elif not isinstance(kind, str) or not kind:
        return {'rejected': True, 'reason': 'message type missing', 'provider_msg_id': wamid}
    return {'kind': 'message', 'provider_event_id': _provider_key('message', wamid),
            'provider_msg_id': wamid, 'sender': sender, 'msg_type': kind, 'body': body,
            'event_time': _event_time(message.get('timestamp')), 'phone_number_id': metadata.get('phone_number_id')}


def _extract_receipt(status, metadata):
    if not isinstance(status, dict):
        return {'rejected': True, 'reason': 'receipt entry must be an object'}
    wamid = status.get('id')
    key = status.get('status')
    if not isinstance(wamid, str) or not wamid or len(wamid) > 100:
        return {'rejected': True, 'reason': 'receipt id missing or oversized'}
    if key not in ('sent', 'delivered', 'read', 'failed'):
        return {'rejected': True, 'reason': 'receipt status unknown', 'provider_msg_id': wamid}
    errors = status.get('errors') if isinstance(status.get('errors'), list) else []
    return {'kind': 'receipt', 'provider_event_id': _provider_key('receipt', wamid, key),
            'provider_msg_id': wamid, 'status_key': key, 'errors': errors,
            'event_time': _event_time(status.get('timestamp')), 'phone_number_id': metadata.get('phone_number_id')}


def _payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def ingest_batch(cur, events, raw_payloads, now):
    """Persist validated events with identity dedupe/conflict handling.

    Returns per-event outcomes: stored / replay / conflict / rejected. The
    stored payload IS the replay input; same id + new hash quarantines the
    arrival and never mutates the original row.
    """
    outcomes = []
    for event, payload in zip(events, raw_payloads):
        if event.get('rejected'):
            if event.get('provider_msg_id'):
                outcomes.append(_store_rejected(cur, event, payload, now))
            else:
                outcomes.append({'outcome': 'rejected', 'reason': event['reason']})
            continue
        digest = _payload_hash(payload)
        row = cur.execute('SELECT provider_event_id, payload_hash, status FROM soloestate.webhook_events '
                          'WHERE provider_event_id=%s', (event['provider_event_id'],)).fetchone()
        if row is None:
            cur.execute(
                """INSERT INTO soloestate.webhook_events(provider_event_id,channel,event_kind,provider_msg_id,
                    status_key,received_at,event_time,payload,payload_hash,status)
                    VALUES (%s,'whatsapp',%s,%s,%s,%s,%s,%s,%s,'received')""",
                (event['provider_event_id'], event['kind'], event['provider_msg_id'], event.get('status_key'),
                 now, event['event_time'], json.dumps(payload), digest))
            outcomes.append({'outcome': 'stored', 'provider_event_id': event['provider_event_id']})
        elif row['payload_hash'] == digest:
            outcomes.append({'outcome': 'replay', 'provider_event_id': event['provider_event_id']})
        else:
            kept = cur.execute('SELECT count(*) AS n FROM soloestate.webhook_conflicts WHERE provider_event_id=%s',
                               (event['provider_event_id'],)).fetchone()['n']
            if kept < 10:
                cur.execute('INSERT INTO soloestate.webhook_conflicts(provider_event_id,payload,payload_hash,received_at) '
                            'VALUES (%s,%s,%s,%s)',
                            (event['provider_event_id'], json.dumps(payload), digest, now))
            else:
                cur.execute('UPDATE soloestate.webhook_events SET conflict_count=conflict_count+1 '
                            'WHERE provider_event_id=%s', (event['provider_event_id'],))
            outcomes.append({'outcome': 'conflict', 'provider_event_id': event['provider_event_id']})
    return outcomes


def _store_rejected(cur, event, payload, now):
    key = 'evt:rejected:' + str(uuid4())
    cur.execute(
        """INSERT INTO soloestate.webhook_events(provider_event_id,channel,event_kind,provider_msg_id,
            received_at,payload,payload_hash,status,reject_reason)
            VALUES (%s,'whatsapp',%s,%s,%s,%s,%s,'rejected',%s)""",
        (key, event.get('kind') or 'message', event['provider_msg_id'], now,
         json.dumps(payload if isinstance(payload, dict) else {}), _payload_hash(payload or {}), event['reason']))
    return {'outcome': 'rejected', 'provider_event_id': key, 'reason': event['reason']}


def claim_events(cur, owner, limit=10, lease_seconds=LEASE_SECONDS):
    cur.execute(
        """UPDATE soloestate.webhook_events SET status='claimed', lease_owner=%s,
            lease_until=clock_timestamp()+make_interval(secs=>%s),
            claim_generation=claim_generation+1
            WHERE provider_event_id IN (
                SELECT provider_event_id FROM soloestate.webhook_events
                WHERE status='received' ORDER BY received_at LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING provider_event_id""",
        (owner, lease_seconds, limit))
    return [row['provider_event_id'] for row in cur.fetchall()]


def _claim_generation(cur, provider_event_id):
    row = cur.execute('SELECT claim_generation FROM soloestate.webhook_events WHERE provider_event_id=%s',
                      (provider_event_id,)).fetchone()
    return row['claim_generation'] if row else None


def sweep_expired(cur, owner, limit=50):
    """Return expired-lease events to received (attempts<3) or park them dead."""
    cur.execute(
        """UPDATE soloestate.webhook_events SET status='dead', lease_owner=NULL, lease_until=NULL
            WHERE status='claimed' AND lease_until < clock_timestamp() AND attempts >= %s
            RETURNING provider_event_id""", (MAX_ATTEMPTS,))
    dead = [row['provider_event_id'] for row in cur.fetchall()]
    cur.execute(
        """UPDATE soloestate.webhook_events SET status='received', lease_owner=NULL, lease_until=NULL
            WHERE status='claimed' AND lease_until < clock_timestamp() AND attempts < %s
            RETURNING provider_event_id""", (MAX_ATTEMPTS,))
    return {'dead': dead, 'released': [row['provider_event_id'] for row in cur.fetchall()]}


def _open_conversation_for_link(cur, lead_id):
    row = cur.execute("""SELECT id FROM soloestate.conversations WHERE lead_id=%s AND status='open'
        ORDER BY created_at DESC LIMIT 1""", (lead_id,)).fetchone()
    if row:
        return row['id']
    return cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id,channel) VALUES (%s,%s,%s,%s) '
                       'RETURNING id', (uuid4(), lead_id, None, 'whatsapp')).fetchone()['id']


def project_inbound(cur, *, wamid, sender, msg_type, body, event_time, received_at):
    """Resolve sender identity and persist one inbound message with channel evidence."""
    from app import identity_links
    op_key = 'wa:inbound:' + wamid
    # The observe activity must share the inbound op key: lead_activities.request_key
    # is a deferred FK to crm_operations, and this transaction inserts no other op row.
    link, _created = identity_links.observe(cur, 'whatsapp', sender, op_key, actor='channel')
    convo = _open_conversation_for_link(cur, link['lead_id'])
    cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s AND channel IS NULL", (convo,))
    from app.chat import RATE_LIMIT_PER_HOUR
    # Serialize per conversation first so the rate count, duplicate check,
    # and seq assignment all read one consistent state.
    cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (convo,))
    recent = cur.execute("""SELECT count(*) AS n FROM soloestate.conversation_messages
        WHERE conversation_id=%s AND role='customer' AND created_at > clock_timestamp()-make_interval(hours=>1)""",
                         (convo,)).fetchone()['n']
    if recent >= RATE_LIMIT_PER_HOUR:
        return {'conversation_id': str(convo), 'link_id': str(link['id']), 'replied': False,
                'reason': 'rate_limited'}
    if cur.execute('SELECT 1 FROM soloestate.crm_operations WHERE request_key=%s', (op_key,)).fetchone():
        # Idempotent replay input: recover the already-projected message so a
        # retry resumes the missing turn instead of skipping it. The message id
        # is the persisted association between this event and its projection.
        existing = cur.execute('SELECT id, conversation_id FROM soloestate.conversation_messages '
                               'WHERE request_key=%s', (op_key,)).fetchone()
        if existing is None:
            return {'conversation_id': str(convo), 'link_id': str(link['id']), 'duplicate': True,
                    'message_id': None}
        return {'conversation_id': str(existing['conversation_id']), 'link_id': str(link['id']),
                'duplicate': True, 'message_id': str(existing['id'])}
    seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                      'WHERE conversation_id=%s', (convo,)).fetchone()['next']
    msg_id = uuid4()
    unsupported = msg_type not in SUPPORTED_INBOUND_TYPES
    stored_body = body if not unsupported else '[Unsupported WhatsApp %s message; kept for owner review]' % msg_type
    from psycopg.types.json import Jsonb
    response = {'provider_ts': event_time.isoformat() if event_time else None,
                'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender, 'link_id': str(link['id'])},
                'unsupported_media': unsupported or None}
    cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                'VALUES (%s,%s,%s,%s,%s,%s)',
                (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
    cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key,created_at) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (msg_id, convo, seq, 'customer', stored_body, op_key, received_at))
    return {'conversation_id': str(convo), 'link_id': str(link['id']), 'message_id': str(msg_id),
            'unsupported': unsupported}


def process_message(conn_factory, provider_event_id, *, turn=None, model=None, submit=None,
                    owner=None, generation=None):
    """Durable inbound protocol over separate connections (never one shared
    uncommitted connection across inference):

    Phase 1 commits the inbound projection, so the turn that follows always
    observes committed rows. Phase 2 runs the shared receptionist turn on its
    own connections (inference never runs inside the projection transaction
    and no row locks are held across it). Phase 3 marks the event processed
    only when the turn evaluated the committed message: a duplicate projection
    resumes the missing turn via the recovered message id, a persisted
    unsupported message is a terminal refusal, and message_not_found is a
    retryable visibility artifact, never success. Rate-limited arrivals persist
    nothing and release for retry. Provider submission stays behind the
    dispatcher drive after completion; drive outcomes never gate completion.

    Every worker-originated mutation (error release, dead park, completion,
    receipt completion) is fenced by the claim identity: conditional updates
    requiring the claimed state plus matching (lease_owner, claim_generation),
    with RETURNING splitting winners from stale callers (not_claimed).
    """
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT * FROM soloestate.webhook_events WHERE provider_event_id=%s',
                              (provider_event_id,)).fetchone()
            if row is None or row['status'] != 'claimed':
                return {'processed': False, 'reason': 'not_claimed'}
            if owner is not None and row['lease_owner'] != owner:
                return {'processed': False, 'reason': 'not_claimed'}
            if generation is not None and row['claim_generation'] != generation:
                return {'processed': False, 'reason': 'not_claimed'}
            payload = row['payload']
            message = (payload.get('messages') or [None])[0] or {}
            projected = project_inbound(
                cur, wamid=row['provider_msg_id'], sender=message.get('from', ''),
                msg_type=message.get('type', ''), body=((message.get('text') or {}).get('body', '')
                if message.get('type') == 'text' else ''),
                event_time=row['event_time'], received_at=row['received_at'])
    if projected.get('duplicate') and not projected.get('message_id'):
        if not _release(conn_factory, provider_event_id, owner=owner, generation=generation):
            return {'processed': False, 'reason': 'not_claimed'}
        return {'processed': False, 'reason': 'retry', 'detail': 'projection without message'}
    if not projected.get('replied', True):
        if not _release(conn_factory, provider_event_id, owner=owner, generation=generation):
            return {'processed': False, 'reason': 'not_claimed'}
        return {'processed': False, 'reason': 'retry', 'detail': projected.get('reason', 'rate_limited')}
    reply = None
    if not projected.get('unsupported') and projected.get('message_id'):
        # Fresh projections and duplicate replays both run the turn against
        # committed rows: a retry resumes the missing turn, and an already
        # completed turn replays its stored reply without duplicating it.
        handler = turn
        if handler is None:
            from app import receptionist
            handler = receptionist.respond_turn
        reply = handler(conn_factory, projected['conversation_id'],
                        projected['message_id'], model=model)
    if reply is not None and reply.get('reason') == 'message_not_found':
        if not _release(conn_factory, provider_event_id, owner=owner, generation=generation):
            return {'processed': False, 'reason': 'not_claimed'}
        return {'processed': False, 'reason': 'retry', 'detail': 'message_not_found'}
    result = _complete_event(conn_factory, provider_event_id, owner, generation,
                             {'processed': True, 'projection': projected, 'reply': reply, 'driven': []})
    if result['processed'] and reply is not None and reply.get('replied') and submit is not None:
        from app import dispatch
        result['driven'] = dispatch.drive_conversation(
            conn_factory, projected['conversation_id'],
            worker='process-now:' + provider_event_id, submit=submit)
    return result


def _complete_event(conn_factory, provider_event_id, owner, generation, result):
    """Mark the event processed only while this worker still owns the claim.

    The conditional update is the fencing transition: a stale worker (swept
    lease, newer generation, completed outcome) matches no row, changes
    nothing, and returns not_claimed; the winner already drove delivery and
    row-state idempotency covers the turn replay.
    """
    with conn_factory() as conn:
        with conn.cursor() as cur:
            won = cur.execute(
                "UPDATE soloestate.webhook_events SET status='processed', lease_owner=NULL, lease_until=NULL "
                'WHERE provider_event_id=%s AND status=%s AND lease_owner IS NOT DISTINCT FROM %s '
                'AND claim_generation=%s RETURNING provider_event_id',
                (provider_event_id, 'claimed', owner, generation)).fetchone()
            if won is None:
                return {'processed': False, 'reason': 'not_claimed'}
    return result


def process_receipt(conn_factory, provider_event_id, *, reconcile=None, owner=None, generation=None):
    """Process one claimed receipt under full claim fencing: a stale worker
    never reconciles and never completes; completion is conditional on the
    claimed state plus matching (lease_owner, claim_generation)."""
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT * FROM soloestate.webhook_events WHERE provider_event_id=%s FOR UPDATE',
                              (provider_event_id,)).fetchone()
            if row is None or row['status'] != 'claimed':
                return {'processed': False, 'reason': 'not_claimed'}
            if owner is not None and row['lease_owner'] != owner:
                return {'processed': False, 'reason': 'not_claimed'}
            if generation is not None and row['claim_generation'] != generation:
                return {'processed': False, 'reason': 'not_claimed'}
            handler = reconcile
            if handler is None:
                from app import dispatch
                handler = dispatch.reconcile_receipt
            outcome = handler(cur, row['provider_msg_id'], row['status_key'], row['event_time'], row['payload'],
                              provider_event_id)
            won = cur.execute("UPDATE soloestate.webhook_events SET status='processed', lease_owner=NULL, "
                              'lease_until=NULL WHERE provider_event_id=%s AND status=%s '
                              'AND lease_owner IS NOT DISTINCT FROM %s AND claim_generation=%s '
                              'RETURNING provider_event_id',
                              (provider_event_id, 'claimed', owner, generation)).fetchone()
            if won is None:
                return {'processed': False, 'reason': 'not_claimed'}
            return {'processed': True, 'outcome': outcome}


def process_now(conn_factory, *, owner=None, turn=None, reconcile=None, model=None, submit=None, limit=10):
    """Claim received events and process each in its own transaction.

    Transient failures increment attempts and release the row for sweep
    retry; rows reaching MAX_ATTEMPTS park as dead. Returns per-event results.
    """
    from psycopg.rows import dict_row
    owner = owner or ('process-now:' + str(uuid4()))
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            claimed = claim_events(cur, owner, limit=limit)
            generations = {event_id: _claim_generation(cur, event_id) for event_id in claimed}
    results = []
    for event_id in claimed:
        generation = generations[event_id]
        try:
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    kind = cur.execute('SELECT event_kind FROM soloestate.webhook_events WHERE provider_event_id=%s',
                                       (event_id,)).fetchone()['event_kind']
            if kind == 'message':
                results.append(process_message(conn_factory, event_id, turn=turn, model=model, submit=submit,
                                               owner=owner, generation=generation))
            else:
                results.append(process_receipt(conn_factory, event_id, reconcile=reconcile,
                                               owner=owner, generation=generation))
        except CRMError as exc:
            if exc.status and 400 <= exc.status < 500:
                won = _park_dead(conn_factory, event_id, owner=owner, generation=generation)
                results.append({'processed': False, 'reason': 'dead' if won else 'not_claimed',
                                'detail': str(exc)})
            else:
                won = _release(conn_factory, event_id, owner=owner, generation=generation)
                results.append({'processed': False, 'reason': 'retry' if won else 'not_claimed',
                                'detail': str(exc)})
        except Exception as exc:  # crash safety: never lose the event
            won = _release(conn_factory, event_id, owner=owner, generation=generation)
            results.append({'processed': False, 'reason': 'retry' if won else 'not_claimed',
                            'detail': type(exc).__name__})
    return results


def _park_dead(conn_factory, event_id, *, owner=None, generation=None):
    """Conditionally park a claimed event dead; only the current claim holder
    may transition it. Returns whether this caller won the transition — a
    stale worker changes nothing (status, counters, lease, winner's result).
    """
    with conn_factory() as conn:
        with conn.cursor() as cur:
            return cur.execute(
                "UPDATE soloestate.webhook_events SET status='dead', lease_owner=NULL, lease_until=NULL "
                'WHERE provider_event_id=%s AND status=%s AND lease_owner IS NOT DISTINCT FROM %s '
                'AND claim_generation=%s RETURNING provider_event_id',
                (event_id, 'claimed', owner, generation)).fetchone() is not None


def _release(conn_factory, event_id, *, owner=None, generation=None):
    """Conditionally release a claimed event for bounded retry; only the
    current claim holder may mutate it (attempts, lease, status). Returns
    whether this caller won the transition.
    """
    with conn_factory() as conn:
        with conn.cursor() as cur:
            return cur.execute(
                """UPDATE soloestate.webhook_events
                    SET attempts=attempts+1, lease_owner=NULL, lease_until=NULL,
                        status=CASE WHEN attempts+1 >= %s THEN 'dead' ELSE 'received' END
                    WHERE provider_event_id=%s AND status=%s AND lease_owner IS NOT DISTINCT FROM %s
                    AND claim_generation=%s RETURNING provider_event_id""",
                (MAX_ATTEMPTS, event_id, 'claimed', owner, generation)).fetchone() is not None
