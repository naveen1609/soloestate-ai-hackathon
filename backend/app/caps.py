"""Delivery cap reservation/accounting and cooldown evaluation. D1: caps table plus
pure helpers over supplied attempt/send counts; attempt wiring arrives in D2.
"""
from datetime import date, timedelta


def cooldown_ok(open_attempts, acked_in_window):
    """Cooldown passes only with no open attempt and no acknowledged send in-window."""
    return open_attempts == 0 and acked_in_window == 0


def reserve_cap(cur, day, channel, cap):
    """Atomically consume one unit of the (day, channel) cap. Returns True iff reserved."""
    row = cur.execute(
        """INSERT INTO soloestate.delivery_caps(day,channel,used,cap) VALUES (%s,%s,1,%s)
           ON CONFLICT (day,channel) DO UPDATE SET used=soloestate.delivery_caps.used+1
           WHERE soloestate.delivery_caps.used<soloestate.delivery_caps.cap
           RETURNING used""", (day, channel, cap)).fetchone()
    return row is not None


def refund_cap(cur, day, channel):
    """Return one unit after blocked/abandoned/definitively-failed outcomes."""
    cur.execute("""UPDATE soloestate.delivery_caps SET used=GREATEST(used-1,0)
        WHERE day=%s AND channel=%s""", (day, channel))


def _thread_bound(cur, channel, link, conversation_id):
    """True when the claimed thread row exists and belongs to the link's lead,
    with website handles additionally pinned to the exact conversation."""
    if conversation_id is None:
        return False
    convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (conversation_id,)).fetchone()
    if convo is None or str(convo['lead_id']) != str(link['lead_id']):
        return False
    if channel == 'website' and link['provider_ref'] != 'web:' + str(convo['id']):
        return False
    return True


def _latest_qualifying_inbound(cur, channel, provider_ref, link_id, conversation_id, now):
    """Latest effective instant of persisted authenticated inbound for one identity on a thread.

    Server-owned ingestion contract: every authenticated-ingress operation
    response carries `inbound_evidence` = {channel, provider_ref, link_id},
    stamped server-side at ingress — chat appends (website, handle pinned to
    the token's conversation, live link id or null) and D2 webhooks (whatsapp,
    provider-attested wa_id, live link id after observe()). No client input
    can write it. A message qualifies only as a customer-role row whose
    operation actor matches the channel (visitor for website, channel for
    whatsapp) AND whose evidence envelope exactly matches the requested
    channel, sender and live link generation: website rows never authorize
    WhatsApp, another sender's rows never authorize this sender, and
    revoked/superseded-generation rows never authorize a rebinding.
    Owner-authored, system and outbound rows qualify nothing. Effective time
    is the provider event instant when the ingestion envelope carries one (a
    late webhook arrival cannot manufacture a fresh window), else the
    persisted receive instant. Rows received in the future, with unparseable
    provider timestamps, provider-dated in the future, or provider-dated
    after their own receipt (incoherent clocks) are invalid and skipped, as
    are rows with missing or ambiguous evidence. Returns None when nothing
    qualifies.
    """
    from datetime import datetime, timezone
    rows = cur.execute("""SELECT m.created_at AS received_at, o.actor AS actor, o.response AS response
        FROM soloestate.conversation_messages m
        JOIN soloestate.crm_operations o ON o.request_key = m.request_key
        WHERE m.conversation_id=%s AND m.role='customer' AND o.actor IN ('visitor','channel')
        AND m.created_at <= %s AND m.created_at > %s - make_interval(hours=>24)""",
        (conversation_id, now, now)).fetchall()
    want_actor = 'visitor' if channel == 'website' else 'channel'
    best = None
    for row in rows:
        if row['actor'] != want_actor:
            continue
        evidence = (row['response'] or {}).get('inbound_evidence')
        if not isinstance(evidence, dict) or evidence.get('channel') != channel \
                or evidence.get('provider_ref') != provider_ref \
                or evidence.get('link_id') != str(link_id):
            continue
        received = row['received_at']
        event = received
        raw = (row['response'] or {}).get('provider_ts')
        if raw is not None:
            try:
                event = datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            if event.tzinfo is None:
                event = event.replace(tzinfo=timezone.utc)
            if event > now or event > received:
                continue
        if best is None or event > best:
            best = event
    return best


def _thread_fresh(cur, channel, link, conversation_id, now):
    """True when the thread's latest qualifying inbound for this exact link
    generation is strictly within 24h of the database clock. An event exactly
    on the boundary has lapsed."""
    best = _latest_qualifying_inbound(cur, channel, link['provider_ref'], link['id'], conversation_id, now)
    return best is not None and best > now - timedelta(hours=24)


def authorize_delivery(cur, *, channel, destination, link, lead, category, day, cap,
                       conversation_id=None, policy=None, open_attempts=0, acked_in_window=0):
    """Delivery authorization evaluator (read-only; D2 calls it under locks at
    staging and at the submitting transition). Returns authorized/reasons/basis.

    Identity and thread bindings are re-read from persisted rows here: the
    caller supplies the claimed link row, lead row and thread id, and this
    function verifies channel, lead, destination, thread binding and freshness
    itself. No caller assertion can establish a basis. Thread freshness comes
    only from persisted authenticated inbound whose server-stamped evidence
    envelope exactly matches the requested channel, attested sender and live
    link generation on the claimed conversation, evaluated on one database-clock
    instant; identity age, owner/system/outbound rows, cross-channel rows,
    cross-sender rows, stale-generation rows, envelope-less rows and invalid
    timestamps qualify nothing. Lead contact fields come from the supplied row,
    which the caller must have read under the section-11 lead lock.
    """
    from app.crm import scalar
    from app.identity_links import normalize_sender
    from app.policies import ROUTINE_CATEGORIES
    reasons = []
    now = scalar(cur.execute('SELECT clock_timestamp() AS now').fetchone())
    if scalar(cur.execute("SELECT emergency_stop FROM soloestate.automation_controls WHERE id='default'").fetchone()):
        reasons.append('emergency_stop')
    if scalar(cur.execute('SELECT EXISTS(SELECT 1 FROM soloestate.suppression_list WHERE channel=%s AND destination=%s)',
                          (channel, destination)).fetchone()):
        reasons.append('suppressed')
    if lead['archived_at'] is not None:
        reasons.append('archived_lead')
    if lead['contact_permission'] == 'opted_out' or lead['contact_preference'] == 'none' \
            or lead['handoff_state'] in ('requested', 'in_progress'):
        reasons.append('contact_blocked')
    fresh = None
    if link is not None and link.get('id') is not None:
        fresh = cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s', (link['id'],)).fetchone()
    basis = None
    if fresh is None or fresh['status'] not in ('active', 'observed') \
            or fresh['channel'] != channel or str(fresh['lead_id']) != str(lead['id']):
        reasons.append('destination_unauthorized')
    elif fresh['status'] == 'active':
        if fresh['verified_destination'] == destination:
            basis = 'link'
        else:
            reasons.append('destination_unauthorized')
    else:
        bound = _thread_bound(cur, channel, fresh, conversation_id)
        young = _thread_fresh(cur, channel, fresh, conversation_id, now) if bound else False
        if category in ROUTINE_CATEGORIES and bound and young \
                and destination == normalize_sender(channel, fresh['provider_ref']):
            basis = 'in_thread'
        elif not bound:
            reasons.append('thread_mismatch')
        elif not young:
            reasons.append('thread_expired')
        else:
            reasons.append('destination_unauthorized')
    if basis == 'link' and conversation_id is not None \
            and not _thread_bound(cur, channel, fresh, conversation_id):
        reasons.append('thread_mismatch')
        basis = None
    permission = lead['whatsapp_permission'] if channel == 'whatsapp' else lead['contact_permission']
    if basis == 'link' and permission != 'opted_in':
        reasons.append('permission_required')
    if category not in ROUTINE_CATEGORIES:
        reasons.append('sensitive_category')
    if policy is not None and not policy['enabled']:
        reasons.append('policy_disabled')
    used = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE day=%s AND channel=%s', (day, channel)).fetchone()
    if used is not None and scalar(used) >= cap:
        reasons.append('cap_exhausted')
    if not cooldown_ok(open_attempts, acked_in_window):
        reasons.append('cooldown_active')
    return {'authorized': not reasons, 'reasons': reasons, 'basis': basis}
