"""Durable outbound dispatcher (D2, §10-§11).

One outbox row per logical message; attempts carry the claim/fencing
protocol. Staging reserves caps and commits a `staged` attempt (provider
call provably never made); the `staged → submitting` transition commits
the delivery authorization linearization point (full gate recheck on
current rows, single winner); anything past `staged` without an outcome
is `unknown` and owner-mediated only. Exactly-once delivery is NOT
claimed: each attempt submits at most once, and additional attempts
arise only from bounded definitive-failure retries or explicit
owner-authorized resends with disclosed duplicate risk.
"""
import hashlib
import json

from app import crm_schemas as s
from app.caps import authorize_delivery, lead_delivery_counts, refund_cap, reserve_cap
from app.crm import CRMError
from app.policies import COOLDOWN_HOURS_DEFAULT, DAY_CAP_DEFAULT, ROUTINE_CATEGORIES

OutboxResolve = s.OutboxResolve
OutboxRequeue = s.OutboxRequeue

MAX_ATTEMPTS_PER_OUTBOX = 3
LEASE_SECONDS = 300

# Catalog reply categories mapped onto policy message categories. Every
# catalog id maps to a routine category; sensitive content never stages.
CATEGORY_MAP = {
    'welcome': 'welcome', 'qualification': 'qualification', 'recommendation': 'recommendation',
    'faq': 'faq_answer', 'clarification': 'clarification', 'availability': 'availability',
    'booking_confirmed': 'booking_confirmation', 'handoff': 'acknowledgement', 'fallback': 'clarification',
}

TERMINAL_OUTBOX = frozenset({'sent', 'delivered', 'read', 'failed', 'dead', 'unknown', 'skipped'})


class SubmitFailed(Exception):
    """Definitive provider rejection: safe to bound-retry with a new attempt."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class SubmitUnknown(Exception):
    """May-have-submitted: owner mediation only, never auto-resumed."""

    def __init__(self, reason, provider_msg_id=None):
        self.reason = reason
        self.provider_msg_id = provider_msg_id
        super().__init__(reason)


def stable_key(channel, purpose, source_id):
    return 'outbox:%s:%s:%s' % (channel, purpose, source_id)


def _payload_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _business_day(cur, policy_tz):
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    if policy_tz:
        return now.astimezone(ZoneInfo(policy_tz)).date()
    return now.astimezone(timezone.utc).date()


def stage_in_txn(cur, *, request_key, channel, purpose, source_id, lead_id, conversation_id,
                 link_id, destination, category, payload, content_ref, catalog_version,
                 fact_deps=None):
    """Stage one logical message inside the caller's transaction.

    Returns {'created', 'record', 'replayed'|'skipped'|...}. Authorization
    blocks and cap exhaustion persist owner-visible `skipped` rows with
    reasons instead of raising, so the caller keeps its own commit. Only
    a conflicting payload under an existing key raises (zero writes).

    `fact_deps=None` means the caller performed no fact validation: the
    row is stored as unverified (fact_validated=false, like a pre-0018
    legacy row) and the submission boundary will fail it closed. A
    caller that validated the selection passes the pin list it computed
    (an empty list for a genuinely fact-free validated reply); only that
    explicit evidence marks the row validated.
    """
    from psycopg.types.json import Jsonb
    if category not in ROUTINE_CATEGORIES:
        raise CRMError('Refusing to stage a non-routine category.', 422)
    existing = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE request_key=%s',
                           (request_key,)).fetchone()
    if existing is not None:
        if _payload_hash(existing['payload']) != _payload_hash(payload):
            raise CRMError('Outbox key is already bound to a different payload.')
        return {'created': False, 'record': existing, 'replayed': True}
    link = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s FOR UPDATE', (link_id,)).fetchone() \
        if link_id is not None else None
    lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE', (lead_id,)).fetchone()
    if lead is None:
        raise CRMError('Active lead not found.', 404)
    reply = cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s FOR UPDATE',
                        (channel,)).fetchone()
    policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
    policy_version = policy['version'] if policy else 0
    day = _business_day(cur, policy['timezone'] if policy else None)
    reason = None
    basis = None
    if link is None or link['status'] not in ('observed', 'active'):
        reason = 'destination_unauthorized'
    elif reply is None:
        reason = 'reply_policy_missing'
    else:
        from app.caps import _thread_fresh
        # D2 auto-sends are replies: free-text provider delivery requires a
        # fresh thread, so a stale thread skips instead of staging.
        if not _thread_fresh(cur, channel, link, conversation_id,
                             cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']):
            reason = 'thread_expired'
        else:
            counts = lead_delivery_counts(cur, lead_id, window_hours=COOLDOWN_HOURS_DEFAULT)
            auth = authorize_delivery(cur, channel=channel, destination=destination, link=link, lead=lead,
                                      category=category, day=day, cap=DAY_CAP_DEFAULT,
                                      conversation_id=conversation_id, policy=reply,
                                      open_attempts=counts['open_attempts'],
                                      acked_in_window=counts['acked_in_window'])
            if not auth['authorized']:
                reason = ','.join(auth['reasons'])
            else:
                basis = auth['basis']
    if reason is None and not reserve_cap(cur, day, channel, DAY_CAP_DEFAULT):
        reason = 'cap_exhausted'
    permission = lead['whatsapp_permission'] if channel == 'whatsapp' else lead['contact_permission']
    consent = {'permission': permission, 'source': 'lead_row',
               'time': cur.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()}
    if reason is not None:
        structural_basis = 'link' if link is not None and link['status'] == 'active' else 'in_thread'
        row = cur.execute("""INSERT INTO soloestate.delivery_outbox(request_key,channel,purpose,source_id,lead_id,
            conversation_id,identity_link_id,destination,verified_destination,destination_basis,category,content_ref,
            policy_version,consent,payload,catalog_version,status,skip_reason,fact_deps,fact_validated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'skipped',%s,%s,%s) RETURNING *""",
                          (request_key, channel, purpose, source_id, lead_id, conversation_id, link_id,
                           destination, link['verified_destination'] if link else None,
                           structural_basis, category, content_ref, policy_version,
                           Jsonb(consent), Jsonb(payload), catalog_version, reason,
                           Jsonb(fact_deps or []), fact_deps is not None)).fetchone()
        return {'created': True, 'record': row, 'skipped': reason}
    row = cur.execute("""INSERT INTO soloestate.delivery_outbox(request_key,channel,purpose,source_id,lead_id,
        conversation_id,identity_link_id,destination,verified_destination,destination_basis,category,content_ref,
        policy_version,reply_policy_id,reply_policy_version,consent,payload,catalog_version,status,reserved_day,reserved,fact_deps,fact_validated)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'staged',%s,true,%s,%s) RETURNING *""",
                      (request_key, channel, purpose, source_id, lead_id, conversation_id, link_id,
                       destination, link['verified_destination'] if link else None,
                       basis, category, content_ref, policy_version, reply['id'], reply['version'],
                       Jsonb(consent), Jsonb(payload), catalog_version, day,
                       Jsonb(fact_deps or []), fact_deps is not None)).fetchone()
    attempt = cur.execute("""INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status)
        VALUES (%s,1,'staged') RETURNING *""", (row['id'],)).fetchone()
    return {'created': True, 'record': row, 'attempt_id': str(attempt['id'])}


def stage(crm, **kwargs):
    """Standalone staging with its own transaction + key advisory lock."""
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('outbox:' + kwargs['request_key'],))
        with conn.cursor(row_factory=dict_row) as cur:
            return stage_in_txn(cur, **kwargs)


def _open_attempt(cur, outbox_id):
    return cur.execute("""SELECT * FROM soloestate.delivery_attempts WHERE outbox_id=%s
        AND status IN ('staged','submitting','unknown') ORDER BY attempt_no DESC LIMIT 1 FOR UPDATE""",
                       (outbox_id,)).fetchone()


def _stale_fact_reasons(cur, outbox):
    """Revalidate the pinned business facts a staged body was rendered from.

    Runs inside the transition transaction after delivery authorization.
    Each fact row is locked in deterministic (type, id) order, after the
    existing protocol locks and before caps accounting moves, so a
    concurrent owner update either committed first (this read sees it
    and blocks) or waits (this submission was authorized on facts still
    true at the linearization point). Only values the body states are
    compared; unrelated edits never invalidate. Returns human-readable
    stale reasons; empty means every fact still holds.
    """
    from decimal import Decimal, InvalidOperation
    deps = outbox.get('fact_deps') or []
    reasons = []
    for dep in sorted(deps, key=lambda d: (str(d.get('type')), str(d.get('id')))):
        kind = dep.get('type')
        ident = dep.get('id')
        try:
            if kind == 'property':
                row = cur.execute('SELECT title,locality,price,bedrooms,active FROM soloestate.properties '
                                  'WHERE id=%s FOR UPDATE', (ident,)).fetchone()
                if row is None:
                    reasons.append('property %s no longer exists' % ident)
                elif not row['active']:
                    reasons.append('property %s deactivated' % ident)
                elif row['title'] != dep['title'] or row['locality'] != dep['locality'] \
                        or Decimal(str(row['price'])) != Decimal(str(dep['price'])) \
                        or int(row['bedrooms']) != int(dep['bedrooms']):
                    reasons.append('property %s details changed' % ident)
            elif kind == 'faq':
                row = cur.execute('SELECT status,current_version FROM soloestate.faqs '
                                  'WHERE id=%s FOR UPDATE', (ident,)).fetchone()
                if row is None or row['status'] != 'active':
                    reasons.append('faq %s unavailable' % ident)
                elif int(row['current_version']) != int(dep['version']):
                    reasons.append('faq %s version changed' % ident)
            elif kind == 'slot':
                row = cur.execute('SELECT starts_at,ends_at,enabled FROM soloestate.visit_slots '
                                  'WHERE id=%s FOR UPDATE', (ident,)).fetchone()
                if row is None or not row['enabled']:
                    reasons.append('slot %s unavailable' % ident)
                elif row['starts_at'].isoformat() != dep['starts_at'] \
                        or row['ends_at'].isoformat() != dep['ends_at']:
                    reasons.append('slot %s time changed' % ident)
            elif kind == 'window':
                row = cur.execute('SELECT status,requested_start,requested_end,customer_confirmed_start,'
                                  'customer_confirmed_end,owner_approved_start,owner_approved_end,booking_id '
                                  'FROM soloestate.booking_requests WHERE id=%s FOR UPDATE',
                                  (ident,)).fetchone()
                if row is None or row['status'] not in ('proposed', 'approved', 'booked', 'awaiting_owner'):
                    reasons.append('booking request %s no longer live' % ident)
                else:
                    start = row['customer_confirmed_start'] or row['owner_approved_start'] or row['requested_start']
                    end = row['customer_confirmed_end'] or row['owner_approved_end'] or row['requested_end']
                    if start.isoformat() != dep['starts_at'] or end.isoformat() != dep['ends_at']:
                        reasons.append('appointment time changed for request %s' % ident)
                    elif row['status'] == 'booked' and row['booking_id'] is not None:
                        slot = cur.execute('SELECT s.starts_at, s.ends_at FROM soloestate.bookings b '
                                           'JOIN soloestate.visit_slots s ON s.id=b.slot_id WHERE b.id=%s',
                                           (row['booking_id'],)).fetchone()
                        if slot is None or slot['starts_at'].isoformat() != dep['starts_at'] \
                                or slot['ends_at'].isoformat() != dep['ends_at']:
                            reasons.append('appointment time changed for request %s' % ident)
            elif kind == 'booking':
                row = cur.execute('SELECT status FROM soloestate.bookings WHERE id=%s FOR UPDATE',
                                  (ident,)).fetchone()
                if row is None:
                    reasons.append('booking %s no longer exists' % ident)
                elif row['status'] != 'confirmed':
                    reasons.append('booking %s cancelled' % ident)
            else:
                reasons.append('fact dependency %s cannot be verified' % kind)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            reasons.append('fact dependency %s cannot be verified' % kind)
    return reasons


def _transition(cur, outbox, attempt, worker, lease_seconds):
    """Commit the staged→submitting linearization point or block.

    Re-locks link → lead → controls → policy → reply-policy → caps (outbox
    already locked by the caller) and rechecks every gate on current rows,
    including the live immediate-reply policy (enabled + category) and
    the pinned business facts the staged body was rendered from. Any
    authorization failure closes the attempt as `blocked`, returns the
    outbox row to `queued`, and refunds the reservation (a stale-facts
    failure instead lands the row owner-visibly `skipped`, because the
    body itself must never be resubmitted): re-staging later is a new
    authorization, never a resumption, because nothing was submitted.
    Cooldown counts the lead's other open attempts plus in-window
    acknowledged sends (this row excluded); the held cap unit is validated
    by ownership, never recharged against headroom. Legacy rows without a
    reply binding fail closed here and are rebound by the next restage.
    """
    now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    if attempt['lease_until'] is not None and attempt['lease_until'] > now \
            and attempt['lease_owner'] != worker:
        return {'transitioned': False, 'reason': 'busy'}
    link = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s FOR UPDATE',
                       (outbox['identity_link_id'],)).fetchone() if outbox['identity_link_id'] else None
    lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE', (outbox['lead_id'],)).fetchone()
    controls = cur.execute("SELECT * FROM soloestate.automation_controls WHERE id='default' FOR UPDATE").fetchone()
    policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
    reply = cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s FOR UPDATE',
                        (outbox['channel'],)).fetchone()
    counts = lead_delivery_counts(cur, outbox['lead_id'], window_hours=COOLDOWN_HOURS_DEFAULT,
                                  exclude_outbox_id=outbox['id'])
    auth = authorize_delivery(cur, channel=outbox['channel'], destination=outbox['destination'], link=link,
                              lead=lead, category=outbox['category'], day=outbox['reserved_day'],
                              cap=DAY_CAP_DEFAULT, conversation_id=outbox['conversation_id'], policy=reply,
                              open_attempts=counts['open_attempts'], acked_in_window=counts['acked_in_window'],
                              reservation={'reserved': bool(outbox['reserved']),
                                           'day': outbox['reserved_day']})
    if controls is None:
        auth = {'authorized': False, 'reasons': auth['reasons'] + ['controls_missing']}
    if policy is None:
        auth = {'authorized': False, 'reasons': auth['reasons'] + ['policy_missing']}
    if reply is None:
        auth = {'authorized': False, 'reasons': auth['reasons'] + ['reply_policy_missing']}
    if outbox['reply_policy_id'] is None:
        auth = {'authorized': False, 'reasons': auth['reasons'] + ['reply_policy_unbound']}
    if not auth['authorized']:
        cur.execute("UPDATE soloestate.delivery_attempts SET status='blocked', outcome=%s WHERE id=%s",
                    (','.join(auth['reasons']), attempt['id']))
        cur.execute("UPDATE soloestate.delivery_outbox SET status='queued', updated_at=clock_timestamp() WHERE id=%s",
                    (outbox['id'],))
        if outbox['reserved']:
            refund_cap(cur, outbox['reserved_day'], outbox['channel'])
            cur.execute('UPDATE soloestate.delivery_outbox SET reserved=false WHERE id=%s', (outbox['id'],))
        return {'transitioned': False, 'reason': 'blocked', 'reasons': auth['reasons']}
    if not outbox.get('fact_validated'):
        # No validation evidence: a pre-0018 legacy row, a pre-0019 row,
        # or a snapshot staged without going through the validated path.
        # Empty pins are indistinguishable from a fact-free validated
        # reply without this evidence, so the row fails closed exactly
        # like a stale one: never submitted, never rewritten, refunded
        # once, fresh generation required. Restage/requeue/resend all
        # funnel through this transition, so none can bypass the rule,
        # and unknown rows never reach it (their handling is unchanged).
        stale = ['fact validation evidence missing (legacy or incomplete '
                 'snapshot); fresh generation required']
    else:
        stale = _stale_fact_reasons(cur, outbox)
    if stale:
        # The staged body asserts facts that no longer hold: never submit
        # it, never silently rewrite it, never requeue it for a later
        # blind resubmission. The row lands owner-visibly skipped with
        # the reason; a fresh turn must regenerate and revalidate.
        note = ('stale_facts: ' + '; '.join(stale))[:500]
        cur.execute("UPDATE soloestate.delivery_attempts SET status='blocked', outcome=%s WHERE id=%s",
                    (note, attempt['id']))
        cur.execute("UPDATE soloestate.delivery_outbox SET status='skipped', skip_reason=%s, "
                    'updated_at=clock_timestamp() WHERE id=%s', (note, outbox['id']))
        if outbox['reserved']:
            refund_cap(cur, outbox['reserved_day'], outbox['channel'])
            cur.execute('UPDATE soloestate.delivery_outbox SET reserved=false WHERE id=%s', (outbox['id'],))
        return {'transitioned': False, 'reason': 'stale', 'reasons': stale}
    row = cur.execute("""UPDATE soloestate.delivery_attempts
        SET status='submitting', lease_owner=%s, lease_until=clock_timestamp()+make_interval(secs=>%s),
            fencing=fencing+1 WHERE id=%s AND status='staged' AND fencing=%s RETURNING *""",
                      (worker, lease_seconds, attempt['id'], attempt['fencing'])).fetchone()
    if row is None:
        return {'transitioned': False, 'reason': 'lost'}
    cur.execute("UPDATE soloestate.delivery_outbox SET status='submitting', updated_at=clock_timestamp() WHERE id=%s",
                (outbox['id'],))
    return {'transitioned': True, 'attempt': row}


def drive_row(conn_factory, outbox_id, *, worker, submit, lease_seconds=LEASE_SECONDS):
    """Drive one outbox row to a resting state. Returns {'status': ...}.

    `submit(recipient, text)` returns the provider message id on accept and
    raises SubmitFailed (definitive) or SubmitUnknown (may-have-submitted).
    Any other exception from submit is treated conservatively as unknown.
    """
    from psycopg.rows import dict_row
    for _ in range(MAX_ATTEMPTS_PER_OUTBOX + 2):
        with conn_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                     (outbox_id,)).fetchone()
                if outbox is None:
                    return {'status': 'missing'}
                if outbox['status'] in TERMINAL_OUTBOX:
                    return {'status': outbox['status']}
                attempt = _open_attempt(cur, outbox_id)
                now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                if attempt is None:
                    restaged = _restage(cur, outbox, worker)
                    if restaged['status'] != 'restaged':
                        return restaged
                    continue
                if attempt['status'] == 'unknown':
                    return {'status': 'unknown'}
                if attempt['status'] == 'submitting':
                    if attempt['lease_until'] is not None and attempt['lease_until'] > now:
                        return {'status': 'busy'}
                    return _mark_unknown(cur, outbox, attempt, 'lease expired while submitting')
                moved = _transition(cur, outbox, attempt, worker, lease_seconds)
                if not moved['transitioned']:
                    result = {'status': moved['reason']}
                    if moved.get('reasons'):
                        result['reasons'] = moved['reasons']
                    return result
                payload = dict(outbox['payload'])
                recipient = outbox['destination']
        try:
            provider_msg_id = submit(recipient, payload['body'])
        except SubmitFailed as exc:
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    failed = _record_failure(cur, outbox_id, moved['attempt']['id'], exc.reason)
                    if failed['status'] != 'retrying':
                        return failed
                    continue
        except SubmitUnknown as exc:
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    if exc.provider_msg_id:
                        _receipt_lock(cur, outbox['channel'], exc.provider_msg_id)
                    outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                         (outbox_id,)).fetchone()
                    attempt = cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE id=%s FOR UPDATE',
                                          (moved['attempt']['id'],)).fetchone()
                    marked = _mark_unknown(cur, outbox, attempt, exc.reason, exc.provider_msg_id)
                    if marked['status'] == 'unknown' and exc.provider_msg_id:
                        _drain_pending_receipts(cur, exc.provider_msg_id, channel=outbox['channel'])
                    return marked
        except Exception as exc:  # noqa: BLE001 - submitter crash is conservatively unknown
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                         (outbox_id,)).fetchone()
                    attempt = cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE id=%s FOR UPDATE',
                                          (moved['attempt']['id'],)).fetchone()
                    return _mark_unknown(cur, outbox, attempt, 'submitter raised %s' % type(exc).__name__)
        with conn_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Hold the receipt lock across ack persistence so early
                # receipts retained as pending are drained here, and a
                # receipt committing concurrently can never be lost. Lock
                # order follows the protocol: advisory, outbox, attempt.
                _receipt_lock(cur, outbox['channel'], provider_msg_id)
                cur.execute('SELECT id FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                            (outbox_id,)).fetchone()
                # The lease may have expired mid-submit (sweeper marked
                # unknown) or the owner may have resolved meanwhile: only a
                # still-submitting attempt accepts the ack, otherwise the
                # provider id is preserved as evidence without moving state.
                acked = cur.execute("""UPDATE soloestate.delivery_attempts SET status='acknowledged',
                    provider_msg_id=%s, submitted_at=clock_timestamp(), outcome='accepted'
                    WHERE id=%s AND status='submitting' RETURNING id""",
                                    (provider_msg_id, moved['attempt']['id'])).fetchone()
                if acked is None:
                    cur.execute("""UPDATE soloestate.delivery_outbox
                        SET provider_msg_id=coalesce(provider_msg_id,%s), updated_at=clock_timestamp()
                        WHERE id=%s""", (provider_msg_id, outbox_id))
                    return {'status': 'resolved_elsewhere', 'provider_msg_id': provider_msg_id}
                cur.execute("""UPDATE soloestate.delivery_outbox SET status='sent', provider_msg_id=%s,
                    updated_at=clock_timestamp() WHERE id=%s""", (provider_msg_id, outbox_id))
                _drain_pending_receipts(cur, provider_msg_id, channel=outbox['channel'])
                return {'status': 'sent', 'provider_msg_id': provider_msg_id}
    return {'status': 'busy'}


def _restage(cur, outbox, worker):
    """Authorize a fresh attempt for a queued row (new authorization)."""
    from app.crm import scalar
    made = scalar(cur.execute('SELECT coalesce(max(attempt_no),0) FROM soloestate.delivery_attempts WHERE outbox_id=%s',
                              (outbox['id'],)).fetchone())
    if made >= MAX_ATTEMPTS_PER_OUTBOX:
        cur.execute("UPDATE soloestate.delivery_outbox SET status='dead', updated_at=clock_timestamp() WHERE id=%s",
                    (outbox['id'],))
        return {'status': 'dead'}
    link = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s FOR UPDATE',
                       (outbox['identity_link_id'],)).fetchone() if outbox['identity_link_id'] else None
    lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE', (outbox['lead_id'],)).fetchone()
    reply = cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s FOR UPDATE',
                        (outbox['channel'],)).fetchone()
    counts = lead_delivery_counts(cur, outbox['lead_id'], window_hours=COOLDOWN_HOURS_DEFAULT,
                                  exclude_outbox_id=outbox['id'])
    held = {'reserved': True, 'day': outbox['reserved_day']} if outbox['reserved'] else None
    auth = authorize_delivery(cur, channel=outbox['channel'], destination=outbox['destination'], link=link,
                              lead=lead, category=outbox['category'], day=outbox['reserved_day'] or
                              _business_day(cur, None), cap=DAY_CAP_DEFAULT,
                              conversation_id=outbox['conversation_id'], policy=reply,
                              open_attempts=counts['open_attempts'], acked_in_window=counts['acked_in_window'],
                              reservation=held)
    if reply is None:
        auth = {'authorized': False, 'reasons': auth['reasons'] + ['reply_policy_missing']}
    if not auth['authorized']:
        # Link-invalid rows (revoked/superseded since enqueue) skip with the
        # contract reason instead of a generic authorization list.
        if 'destination_unauthorized' in auth['reasons']:
            skip_reason = 'skipped_link_invalid'
        else:
            skip_reason = ','.join(auth['reasons'])
        cur.execute("UPDATE soloestate.delivery_outbox SET status='skipped', skip_reason=%s WHERE id=%s",
                    (skip_reason, outbox['id']))
        return {'status': 'skipped', 'reasons': auth['reasons']}
    day = outbox['reserved_day'] or _business_day(cur, None)
    if not outbox['reserved'] and not reserve_cap(cur, day, outbox['channel'], DAY_CAP_DEFAULT):
        return {'status': 'queued'}
    cur.execute('UPDATE soloestate.delivery_outbox SET reserved=true, reserved_day=%s, reply_policy_id=%s, '
                'reply_policy_version=%s WHERE id=%s', (day, reply['id'], reply['version'], outbox['id']))
    cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,%s,%s)',
                (outbox['id'], made + 1, 'staged'))
    cur.execute("UPDATE soloestate.delivery_outbox SET status='staged', updated_at=clock_timestamp() WHERE id=%s",
                (outbox['id'],))
    return {'status': 'restaged'}


def _record_failure(cur, outbox_id, attempt_id, reason):
    """Definitive failure: refund, then bounded retry or dead.

    Lock order follows the protocol: the outbox row is locked before the
    attempt row is written.
    """
    from app.crm import scalar
    row = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE', (outbox_id,)).fetchone()
    attempt_no = scalar(cur.execute('SELECT attempt_no FROM soloestate.delivery_attempts WHERE id=%s',
                                    (attempt_id,)).fetchone())
    cur.execute("UPDATE soloestate.delivery_attempts SET status='failed', outcome=%s WHERE id=%s", (reason, attempt_id))
    if row['reserved']:
        refund_cap(cur, row['reserved_day'], row['channel'])
        cur.execute('UPDATE soloestate.delivery_outbox SET reserved=false WHERE id=%s', (row['id'],))
    if attempt_no >= MAX_ATTEMPTS_PER_OUTBOX:
        cur.execute("UPDATE soloestate.delivery_outbox SET status='dead', updated_at=clock_timestamp() WHERE id=%s",
                    (row['id'],))
        return {'status': 'dead'}
    day = row['reserved_day'] or _business_day(cur, None)
    if not reserve_cap(cur, day, row['channel'], DAY_CAP_DEFAULT):
        cur.execute("UPDATE soloestate.delivery_outbox SET status='queued', updated_at=clock_timestamp() WHERE id=%s",
                    (row['id'],))
        return {'status': 'queued'}
    cur.execute('UPDATE soloestate.delivery_outbox SET reserved=true, reserved_day=%s WHERE id=%s', (day, row['id']))
    cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,%s,%s)',
                (row['id'], attempt_no + 1, 'staged'))
    cur.execute("UPDATE soloestate.delivery_outbox SET status='staged', updated_at=clock_timestamp() WHERE id=%s",
                (row['id'],))
    return {'status': 'retrying'}


def _mark_unknown(cur, outbox, attempt, reason, provider_msg_id=None):
    """Potentially-submitted: unknown, held reservation, never auto-resumed.

    Guarded to still-submitting rows: an owner resolution that landed
    meanwhile (superseded + new attempt, sent, or skipped) is never
    overwritten, which also preserves the single-open-attempt unique.
    """
    hit = cur.execute("""UPDATE soloestate.delivery_attempts SET status='unknown',
        provider_msg_id=coalesce(%s,provider_msg_id), outcome=%s, lease_owner=NULL, lease_until=NULL
        WHERE id=%s AND status='submitting' RETURNING id""",
                      (provider_msg_id, reason, attempt['id'])).fetchone()
    if hit is None:
        return {'status': 'resolved_elsewhere'}
    cur.execute("""UPDATE soloestate.delivery_outbox SET status='unknown', provider_msg_id=coalesce(%s,provider_msg_id),
        updated_at=clock_timestamp() WHERE id=%s AND status='submitting'""",
                (provider_msg_id, outbox['id']))
    return {'status': 'unknown'}


def drive_conversation(conn_factory, conversation_id, *, worker, submit, lease_seconds=LEASE_SECONDS):
    """Drive every non-terminal outbox row of one conversation, oldest first."""
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ids = cur.execute("""SELECT id FROM soloestate.delivery_outbox WHERE conversation_id=%s
                AND status NOT IN ('sent','delivered','read','failed','dead','unknown','skipped')
                ORDER BY created_at, id""", (conversation_id,)).fetchall()
    results = []
    for row in ids:
        results.append({'outbox_id': str(row['id']),
                        **drive_row(conn_factory, row['id'], worker=worker, submit=submit,
                                    lease_seconds=lease_seconds)})
    return results


def sweep(conn_factory, worker, limit=50):
    """Reclaim expired leases: staged releases for re-drive; submitting
    becomes unknown. Unknown rows are never touched (owner-mediated).

    Candidates are unlocked peeks; each row is then processed under the
    protocol order (outbox before attempt) with revalidation, so the
    sweeper never holds attempt locks while acquiring outbox locks and a
    racing driver or owner always wins a clean serialized outcome.
    """
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            staged = cur.execute("""SELECT a.id AS attempt_id, a.outbox_id FROM soloestate.delivery_attempts a
                WHERE a.status='staged' AND a.lease_until IS NOT NULL AND a.lease_until < clock_timestamp()
                ORDER BY a.lease_until, a.id LIMIT %s""", (limit,)).fetchall()
            stale = cur.execute("""SELECT a.id AS attempt_id, a.outbox_id FROM soloestate.delivery_attempts a
                WHERE a.status='submitting' AND a.lease_until < clock_timestamp()
                ORDER BY a.lease_until, a.id LIMIT %s""", (limit,)).fetchall()
            released, unknown = [], []
            for row in staged:
                outbox = cur.execute('SELECT id FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                     (row['outbox_id'],)).fetchone()
                if outbox is None:
                    continue
                attempt = cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE id=%s FOR UPDATE',
                                      (row['attempt_id'],)).fetchone()
                if attempt is None or attempt['status'] != 'staged' or attempt['lease_until'] is None:
                    continue
                if cur.execute("""UPDATE soloestate.delivery_attempts SET lease_owner=NULL, lease_until=NULL
                        WHERE id=%s AND status='staged' AND lease_until < clock_timestamp()
                        RETURNING id""", (row['attempt_id'],)).fetchone() is not None:
                    released.append(str(row['attempt_id']))
            for row in stale:
                outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                     (row['outbox_id'],)).fetchone()
                if outbox is None:
                    continue
                attempt = cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE id=%s FOR UPDATE',
                                      (row['attempt_id'],)).fetchone()
                if attempt is None or attempt['status'] != 'submitting':
                    continue
                if _mark_unknown(cur, outbox, attempt, 'lease expired while submitting')['status'] == 'unknown':
                    unknown.append(str(row['attempt_id']))
    return {'released': released, 'unknown': unknown}


RECEIPT_RANK = {'sent': 0, 'delivered': 1, 'read': 2}


def _receipt_lock(cur, channel, provider_msg_id):
    """Serialize receipt vs acknowledgement for one provider id.

    Taken before any delivery row lock in its transaction, this closes the
    lost-wakeup window: whichever side commits second observes the other's
    state (a persisted provider id, or retained pending receipts).
    """
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ('receipt:%s:%s' % (channel, provider_msg_id),))


LOCK_PROTOCOL = (
    'Delivery lock protocol: webhook_events row (receipt path only), then the '
    'per-provider-id receipt advisory, then delivery_outbox, then its '
    'delivery_attempts, then identity link -> lead -> automation controls -> '
    'business policy -> immediate-reply policy, with delivery caps as a leaf. '
    'Provider-id lookups are unlocked peeks; ownership is re-derived and '
    'revalidated only after the outbox and attempt locks are held.'
)


def _receipt_attempt_peek(cur, provider_msg_id):
    """Unlocked lookup by provider id (never locks, never trusted for writes)."""
    return cur.execute("""SELECT id, outbox_id FROM soloestate.delivery_attempts WHERE provider_msg_id=%s
        ORDER BY submitted_at DESC NULLS LAST, created_at DESC LIMIT 1""",
                       (provider_msg_id,)).fetchone()


def _lock_receipt_rows(cur, provider_msg_id):
    """Lock (outbox, attempt) in protocol order and revalidate ownership.

    The candidate is selected by an unlocked peek, but every fact used for a
    write is re-read under the locks: a stale peek degrades to the monotonic
    guards (superseded, owner-resolved, id mismatch), never to writes on the
    wrong rows. Returns (attempt, outbox) or (None, None) when no attempt
    carries the id.
    """
    peek = _receipt_attempt_peek(cur, provider_msg_id)
    if peek is None:
        return None, None
    outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                         (peek['outbox_id'],)).fetchone()
    attempt = cur.execute('SELECT * FROM soloestate.delivery_attempts WHERE id=%s FOR UPDATE',
                          (peek['id'],)).fetchone()
    if attempt is None or outbox is None:
        return None, None
    return attempt, outbox


def _apply_receipt(cur, attempt, outbox, status_key, *, provider_msg_id, channel):
    """Monotonic application of one matched receipt (locks held by caller).

    Rules: sent/delivered/read advance by rank only; `failed` is terminal —
    it applies only before delivered/read and never resurrects or fails a
    delivered/read row (the refund stands). Receipts for superseded or
    owner-resolved attempts, or whose provider id no longer matches the
    outbox's current id, are retained as stale evidence and never move state.
    """
    if attempt['status'] == 'superseded' \
            or (attempt.get('outcome') or '').startswith('owner_resolved') \
            or outbox['provider_msg_id'] != provider_msg_id or outbox['channel'] != channel:
        return {'matched': True, 'stale': True}
    if status_key == 'failed':
        if outbox['status'] in ('failed', 'dead'):
            return {'matched': True, 'duplicate': True}
        if outbox['status'] not in ('sent', 'unknown'):
            return {'matched': True, 'stale': True}
        cur.execute("UPDATE soloestate.delivery_attempts SET status='failed', outcome='receipt_failed' WHERE id=%s",
                    (attempt['id'],))
        cur.execute("UPDATE soloestate.delivery_outbox SET status='failed', updated_at=clock_timestamp() WHERE id=%s",
                    (outbox['id'],))
        if outbox['reserved']:
            refund_cap(cur, outbox['reserved_day'], outbox['channel'])
            cur.execute('UPDATE soloestate.delivery_outbox SET reserved=false WHERE id=%s', (outbox['id'],))
        return {'matched': True, 'advanced': 'failed'}
    if outbox['status'] in ('failed', 'dead'):
        return {'matched': True, 'duplicate': True}
    if attempt['status'] == 'unknown':
        cur.execute("UPDATE soloestate.delivery_attempts SET status='acknowledged', outcome='late_receipt' WHERE id=%s",
                    (attempt['id'],))
        outbox = cur.execute("UPDATE soloestate.delivery_outbox SET status='sent', updated_at=clock_timestamp() "
                             'WHERE id=%s RETURNING *', (outbox['id'],)).fetchone()
    current = RECEIPT_RANK.get(outbox['status'], -1)
    target = RECEIPT_RANK.get(status_key, -1)
    if target <= current:
        return {'matched': True, 'duplicate': True}
    cur.execute('UPDATE soloestate.delivery_outbox SET status=%s, updated_at=clock_timestamp() WHERE id=%s',
                (status_key, outbox['id'],))
    return {'matched': True, 'advanced': status_key}


def _drain_pending_receipts(cur, provider_msg_id, *, channel='whatsapp'):
    """Apply retained receipts for an id whose attempt just became known.

    Runs under the caller's receipt lock and transaction, in progress order
    (sent, delivered, read, failed) so the monotonic rules evaluate each
    receipt against the final watermark rather than arrival order.
    """
    from psycopg.types.json import Jsonb
    rows = cur.execute(
        """SELECT * FROM soloestate.delivery_receipts
            WHERE channel=%s AND provider_msg_id=%s AND applied_outcome IS NULL
            ORDER BY CASE status_key WHEN 'sent' THEN 0 WHEN 'delivered' THEN 1
                     WHEN 'read' THEN 2 WHEN 'failed' THEN 3 ELSE 50 END, created_at, id""",
        (channel, provider_msg_id)).fetchall()
    outcomes = []
    for row in rows:
        attempt, outbox = _lock_receipt_rows(cur, provider_msg_id)
        if attempt is None:
            break
        outcome = _apply_receipt(cur, attempt, outbox, row['status_key'],
                                 provider_msg_id=provider_msg_id, channel=channel)
        cur.execute('UPDATE soloestate.delivery_receipts SET applied_outcome=%s WHERE id=%s',
                    (Jsonb(outcome), row['id']))
        outcomes.append(outcome)
    return outcomes


def reconcile_receipt(cur, provider_msg_id, status_key, event_time, payload, provider_event_id=None,
                      *, channel='whatsapp'):
    """Apply a provider delivery receipt, or retain it if it is early.

    Matches strictly by provider id within the authorized channel context:
    an unknown attempt without a provider id can never auto-match, and an
    unmatched receipt is persisted as pending evidence (never resubmitted)
    and drained when the id becomes known. Duplicates are idempotent.
    """
    from psycopg.types.json import Jsonb
    _receipt_lock(cur, channel, provider_msg_id)
    attempt, outbox = _lock_receipt_rows(cur, provider_msg_id)
    if attempt is None:
        cur.execute(
            'INSERT INTO soloestate.delivery_receipts(channel,provider_msg_id,status_key,event_time,payload,'
            'provider_event_id) VALUES (%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT (channel,provider_msg_id,status_key) DO NOTHING',
            (channel, provider_msg_id, status_key, event_time, Jsonb(payload or {}), provider_event_id))
        return {'matched': False, 'provider_msg_id': provider_msg_id}
    result = _apply_receipt(cur, attempt, outbox, status_key,
                            provider_msg_id=provider_msg_id, channel=channel)
    cur.execute(
        'INSERT INTO soloestate.delivery_receipts(channel,provider_msg_id,status_key,event_time,payload,'
        'provider_event_id,applied_outcome) VALUES (%s,%s,%s,%s,%s,%s,%s) '
        'ON CONFLICT (channel,provider_msg_id,status_key) DO NOTHING',
        (channel, provider_msg_id, status_key, event_time, Jsonb(payload or {}), provider_event_id,
         Jsonb(result)))
    cur.execute('UPDATE soloestate.delivery_receipts SET applied_outcome=%s '
                'WHERE channel=%s AND provider_msg_id=%s AND status_key=%s AND applied_outcome IS NULL',
                (Jsonb(result), channel, provider_msg_id, status_key))
    _drain_pending_receipts(cur, provider_msg_id, channel=channel)
    return result


def resolve_unknown(crm, outbox_id, request):
    """Owner-mediated resolution of one unknown row (audited, idempotent)."""
    from psycopg.rows import dict_row
    from app.crm import scalar
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                 (outbox_id,)).fetchone()
            if outbox is None:
                raise CRMError('Outbox row not found.', 404)
            if outbox['status'] != 'unknown':
                raise CRMError('Only unknown rows accept owner resolution.')
            attempt = _open_attempt(cur, outbox_id)
            if request.action == 'mark_resolved_sent':
                if not request.evidence.strip():
                    raise CRMError('Resolving as sent requires an evidence note.')
                cur.execute("UPDATE soloestate.delivery_attempts SET status='acknowledged', outcome=%s WHERE id=%s",
                            ('owner_resolved_sent: ' + request.evidence.strip(), attempt['id']))
                updated = cur.execute("""UPDATE soloestate.delivery_outbox SET status='sent',
                    updated_at=clock_timestamp() WHERE id=%s RETURNING *""", (outbox_id,)).fetchone()
                return {'record': updated, 'duplicate_risk': False}
            if request.action == 'abandon':
                cur.execute("UPDATE soloestate.delivery_attempts SET status='superseded', outcome=%s WHERE id=%s",
                            ('abandoned: ' + request.reason.strip(), attempt['id']))
                updated = cur.execute("""UPDATE soloestate.delivery_outbox SET status='skipped',
                    skip_reason=%s, updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                                      ('abandoned: ' + request.reason.strip(), outbox_id)).fetchone()
                if outbox['reserved']:
                    refund_cap(cur, outbox['reserved_day'], outbox['channel'])
                    cur.execute('UPDATE soloestate.delivery_outbox SET reserved=false WHERE id=%s', (outbox_id,))
                return {'record': updated, 'duplicate_risk': False}
            reply = _require_reply_policy(cur, outbox['channel'], outbox['category'])
            made = scalar(cur.execute('SELECT coalesce(max(attempt_no),0) FROM soloestate.delivery_attempts '
                                      'WHERE outbox_id=%s', (outbox_id,)).fetchone())
            day = outbox['reserved_day'] or _business_day(cur, None)
            if outbox['reserved']:
                refund_cap(cur, outbox['reserved_day'], outbox['channel'])
            if not reserve_cap(cur, day, outbox['channel'], DAY_CAP_DEFAULT):
                raise CRMError('Daily cap is exhausted; resolve again tomorrow.', 422)
            cur.execute("UPDATE soloestate.delivery_attempts SET status='superseded', outcome=%s WHERE id=%s",
                        ('superseded by owner resend: ' + request.reason.strip(), attempt['id']))
            cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,%s,%s)',
                        (outbox_id, made + 1, 'staged'))
            updated = cur.execute("""UPDATE soloestate.delivery_outbox SET status='staged', reserved=true,
                reserved_day=%s, resend_count=resend_count+1, reply_policy_id=%s, reply_policy_version=%s,
                updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                                  (day, reply['id'], reply['version'], outbox_id)).fetchone()
            return {'record': updated, 'duplicate_risk': True,
                    'disclosure': 'The original attempt may have submitted; the customer may receive this twice.'}
    return crm.mutate('outbox:resolve', request, work)


def _require_reply_policy(cur, channel, category):
    """Current immediate-reply authorization for a new owner-mediated submission.

    Owner mediation resolves uncertainty; it never waives the live policy.
    Returns the locked policy row or raises with no writes.
    """
    reply = cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s FOR UPDATE',
                        (channel,)).fetchone()
    if reply is None:
        raise CRMError('Immediate-reply policy is missing; automatic delivery is refused.')
    if not reply['enabled']:
        raise CRMError('Immediate replies are disabled; enable the reply policy first.')
    if category not in (reply['categories'] or []):
        raise CRMError('This category is no longer permitted for automatic replies.')
    return reply


def requeue(crm, outbox_id, request):
    """Owner requeue of a dead, failed, or skipped row (audited, idempotent)."""
    from psycopg.rows import dict_row
    from app.crm import scalar
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            outbox = cur.execute('SELECT * FROM soloestate.delivery_outbox WHERE id=%s FOR UPDATE',
                                 (outbox_id,)).fetchone()
            if outbox is None:
                raise CRMError('Outbox row not found.', 404)
            if outbox['status'] not in ('dead', 'failed', 'skipped'):
                raise CRMError('Only dead, failed, or skipped rows accept requeue.')
            if _open_attempt(cur, outbox_id) is not None:
                raise CRMError('An open attempt already exists for this row.')
            reply = _require_reply_policy(cur, outbox['channel'], outbox['category'])
            made = scalar(cur.execute('SELECT coalesce(max(attempt_no),0) FROM soloestate.delivery_attempts '
                                      'WHERE outbox_id=%s', (outbox_id,)).fetchone())
            day = outbox['reserved_day'] or _business_day(cur, None)
            if not outbox['reserved'] and not reserve_cap(cur, day, outbox['channel'], DAY_CAP_DEFAULT):
                raise CRMError('Daily cap is exhausted; requeue again tomorrow.', 422)
            cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,%s,%s)',
                        (outbox_id, made + 1, 'staged'))
            updated = cur.execute("""UPDATE soloestate.delivery_outbox SET status='staged', reserved=true,
                reserved_day=%s, skip_reason=NULL, reply_policy_id=%s, reply_policy_version=%s,
                updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                                  (day, reply['id'], reply['version'], outbox_id)).fetchone()
            return {'record': updated}
    return crm.mutate('outbox:requeue', request, work)


def listing(crm, *, status=None, lead_id=None, page=1, page_size=20):
    from app.crm import encode
    from psycopg.rows import dict_row
    if status is not None and status not in ('queued', 'staged', 'submitting', 'sent', 'delivered', 'read',
                                             'failed', 'unknown', 'dead', 'skipped'):
        raise CRMError('Invalid outbox status.', 422)
    conditions, values = [], []
    if status is not None:
        conditions.append('o.status=%s')
        values.append(status)
    if lead_id is not None:
        conditions.append('o.lead_id=%s')
        values.append(lead_id)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM soloestate.delivery_outbox o{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f"""SELECT o.*, (SELECT count(*) FROM soloestate.delivery_attempts a
                WHERE a.outbox_id=o.id) AS attempts FROM soloestate.delivery_outbox o{clause}
                ORDER BY o.created_at DESC, o.id LIMIT %s OFFSET %s""",
                               [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))
