"""Shared autonomous receptionist turn over the closed repertoire (D2, §5-§7).

WhatsApp and website turns share this engine: the model proposes typed
requirements, booking windows, confirmations and catalog selections, and
the backend validates everything against live rows before rendering or
committing anything. D1 services own every authorization decision; the
turn never invents facts, commitments, or identity.

Backend-composed replies: booking outcomes (confirmation, exception,
confirmation-question) and requirement-completing recommendations are
rendered deterministically from committed rows rather than from model
selections, because their content is fully determined server-side.
"""
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from app import catalog, respond, summaries
from app.crm import CRM, CRMError
from app.qualification import readiness

_MISSING_QUESTION = {'budget_min': 'Q_BUDGET', 'budget_max': 'Q_BUDGET',
                     'preferred_localities': 'Q_LOCALITY', 'bedrooms': 'Q_BHK',
                     'purchase_timeframe': 'Q_TIMEFRAME'}


class _TurnAbort(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _generate(model, prompt):
    if model is None:
        from app.config import Settings
        from app.services.meta_model import MetaModelService
        settings = Settings()

        async def live():
            async with MetaModelService(settings) as service:
                return await service.generate(prompt)
        return asyncio.run(live())
    result = model.generate(prompt)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _sensitive(text):
    lowered = (text or '').lower()
    return [marker for marker in catalog.SENSITIVE_MARKERS if marker in lowered]


def _pending_request(cur, conversation_id, now):
    return cur.execute("""SELECT * FROM soloestate.booking_requests WHERE conversation_id=%s
        AND status IN ('proposed','approved') AND (proposal_expires_at IS NULL OR proposal_expires_at>%s)
        ORDER BY created_at DESC LIMIT 1""", (conversation_id, now)).fetchone()


def respond_turn(conn_factory, conversation_id, message_id, model=None, actor='channel'):
    """Run one autonomous turn for a persisted customer message.

    Returns {'replied': True, ...} with exactly one persisted assistant
    message, or {'replied': False, 'reason': ...} when the turn is frozen,
    escalated-sensitive, or replayed. Booking side effects use
    deterministic keys derived from the message id, so crash recovery
    replays them instead of duplicating them. WhatsApp turns use the
    default 'channel' actor; the website adapter passes 'visitor' so
    visitor-attributed rows keep visitor provenance. Operation action
    names stay shared across channels; only the actor varies.
    """
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (conversation_id,)).fetchone()
            message = cur.execute('SELECT * FROM soloestate.conversation_messages WHERE id=%s AND conversation_id=%s',
                                  (message_id, conversation_id)).fetchone()
            if convo is None or message is None or message['role'] != 'customer':
                return {'replied': False, 'reason': 'message_not_found'}
            lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (convo['lead_id'],)).fetchone()
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    frozen = _frozen_reason(convo, lead)
    if frozen is not None:
        return {'replied': False, 'reason': frozen}
    reply_key = 'channel:reply:' + str(message_id)
    stored = _stored_reply(conn_factory, reply_key)
    if stored is not None:
        return stored
    markers = _sensitive(message['body'])
    if markers:
        try:
            return _escalate_sensitive(conn_factory, convo, lead, message, markers, reply_key, actor)
        except _TurnAbort as exc:
            return {'replied': False, 'reason': exc.reason}
    crm = CRM(conn_factory)
    trace = []
    raws = []
    feedback = None
    ctx, degraded = _ensure_context(conn_factory, conversation_id, model, trace)
    if degraded:
        try:
            return _commit_degraded(crm, conn_factory, convo, lead, message, reply_key, trace, actor)
        except _TurnAbort as exc:
            return {'replied': False, 'reason': exc.reason}
    for _attempt in (1, 2):
        try:
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (convo['lead_id'],)).fetchone()
                    convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s',
                                        (conversation_id,)).fetchone()
                    registry = respond.build_registry(cur, convo, lead, message['body'], dict(policy), now)
                    pending = _pending_request(cur, conversation_id, now)
            supportable, required_entry = _supportable(registry, lead, pending)
            prompt = respond.turn_prompt(
                conversation=convo, lead=lead, context=ctx,
                registry=registry, message_text=message['body'], supportable=supportable,
                required_entry=required_entry, policy=dict(policy), now=now,
                pending_request=_pending_render(pending, dict(policy)) if pending else None,
                extra_feedback=feedback)
            result = _generate(model, prompt)
            raws.append(result.text if result else None)
            try:
                output = respond.parse_output(result.text)
            except respond.InvalidSelection as exc:
                trace.append('parse: %s' % exc)
                feedback = str(exc)
                continue
            outcome = _process_proposals(crm, conn_factory, convo, lead, message, output, registry,
                                         pending, dict(policy), trace, actor)
            if outcome.get('abort'):
                trace.append('proposals: %s' % outcome['abort'])
                feedback = outcome['abort']
                continue
            selected = outcome.get('server_output', output)
            required = outcome.get('forced_category')
            entry = outcome.get('required_entry')
            if required is None:
                supportable, required_entry = _supportable(registry, lead, pending)
                if selected.category not in supportable:
                    feedback = 'category %s is not supportable this turn; supportable: %s' % (
                        selected.category, sorted(supportable))
                    trace.append('category: %s' % feedback)
                    continue
                required = selected.category
                entry = required_entry if required == 'qualification' else None
            registry.update(outcome.get('extra_facts', {}))
            with conn_factory() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    _rendered, errors = respond.validate_selection(
                        cur, selected, registry, required_category=required, required_entry=entry,
                        allowed_actions=outcome.get('allowed_actions', {}),
                        conversation=convo, message_text=message['body'])
            if errors:
                feedback = 'rejected: ' + '; '.join(errors)
                trace.append('selection: %s' % feedback)
                continue
            return _commit_reply(crm, conn_factory, convo, lead, message, selected, output, registry,
                                 outcome, result, reply_key, trace, required, entry, actor)
        except _TurnAbort as exc:
            return {'replied': False, 'reason': exc.reason}
        except CRMError as exc:
            trace.append('service refused: %s' % exc)
            feedback = 'service refused: %s' % exc
            continue
        except Exception as exc:  # noqa: BLE001 - turn errors fall back, never crash the event
            from app.services.meta_model import ModelServiceError
            if isinstance(exc, ModelServiceError) and exc.code == 'configuration':
                trace.append('model unconfigured: %s' % exc)
                break
            diag = getattr(exc, 'diag', None)
            constraint = diag.constraint_name if diag is not None else None
            if constraint:
                label = '%s (%s)' % (type(exc).__name__, constraint)
            elif isinstance(exc, ModelServiceError):
                label = 'model %s' % exc.code
            else:
                label = type(exc).__name__
            trace.append('turn error: %s' % label)
            feedback = 'the turn failed (%s); produce the required selection without guessing facts' % label
            continue
    try:
        return _commit_fallback(crm, conn_factory, convo, lead, message, reply_key, trace, raws, actor)
    except _TurnAbort as exc:
        return {'replied': False, 'reason': exc.reason}


def _frozen_reason(convo, lead):
    if convo['status'] == 'resolved':
        return 'resolved'
    if convo['status'] == 'pending_owner':
        return 'pending_owner'
    if convo['handoff_state'] == 'requested':
        return 'handoff_requested'
    if lead['archived_at'] is not None:
        return 'lead_archived'
    return None


def _stored_reply(conn_factory, reply_key):
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                              (reply_key,)).fetchone()
    if row is None:
        return None
    saved = dict(row['response'].get('reply', {}))
    saved['replay'] = True
    return saved


def _context(conn_factory, conversation_id, **keep):
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return summaries.context(cur, conversation_id, **keep)


def _ensure_context(conn_factory, conversation_id, model, trace):
    """Summarize-before-truncate, or report degradation.

    Returns (context, degraded). On generation failure the turn runs
    tail-only with the degraded flag persisted on its record plus an
    owner-visible activity entry, and clarifies instead of asserting.
    """
    ctx = _context(conn_factory, conversation_id)
    if not ctx['tail_truncated']:
        return ctx, False
    gen = summaries.generate(conn_factory, conversation_id, model)
    trace.append('summary: %s' % ('through %s' % gen.get('through_seq') if gen.get('generated') else gen.get('reason')))
    if not gen.get('generated'):
        return ctx, True
    return _context(conn_factory, conversation_id, keep_messages=summaries.TAIL_KEEP_AFTER_SUMMARY,
                    keep_bytes=summaries.TAIL_KEEP_BYTES), False


def _supportable(registry, lead, pending):
    # Availability is always supportable: booking proposals and slot
    # questions are validated strictly downstream, so listing it can
    # only lead to validated outcomes or rejection, never a bad send.
    kinds = {fact['ftype'] for fact in registry.values()}
    supportable = {'welcome', 'clarification', 'availability'}
    missing = [field for field in readiness(lead)['missing_fields'] if field in _MISSING_QUESTION]
    required_entry = None
    if missing:
        supportable.add('qualification')
        required_entry = _MISSING_QUESTION[missing[0]]
    if 'match_list' in kinds:
        supportable.add('recommendation')
    if 'faq_quotes' in kinds:
        supportable.add('faq')
    return supportable, required_entry


def _pending_render(pending, policy):
    from app.respond import render_window
    if pending['status'] == 'proposed':
        start, end = pending['requested_start'], pending['requested_end']
    else:
        start = pending['customer_confirmed_start'] or pending['owner_approved_start']
        end = pending['customer_confirmed_end'] or pending['owner_approved_end']
    return {'id': str(pending['id']), 'rendered': render_window(start, end, policy['timezone'])}


def _process_proposals(crm, conn_factory, convo, lead, message, output, registry, pending, policy, trace, actor):
    """Validate typed proposals; run booking side effects idempotently.

    Returns outcome dict with forced_category / required_entry /
    server_output / extra_facts / allowed_actions, or {'abort': reason}
    when the turn cannot proceed with these proposals.
    """
    # allowed_actions stays empty: booking outcomes are server-composed,
    # so no current turn lets a model selection reference a committed
    # record. The action_ref schema shape is still enforced (unknown
    # refs reject), reserved for turns that render model selections
    # around turn-created records in the future.
    outcome = {'allowed_actions': {}}
    if output.requirements is not None:
        _apply_requirements(crm, convo, lead, message, output.requirements, trace, actor)
    property_id = convo['property_id']
    if output.select_property is not None:
        fact = registry.get(output.select_property)
        if fact is None or fact['ftype'] != 'property':
            return {'abort': 'select_property must reference a property fact of this turn'}
        property_id = UUID(fact['row_id'])
        outcome['select_property'] = property_id
        trace.append('property selected: %s' % property_id)
    if output.confirm_request is not None:
        return _process_confirm(crm, convo, lead, message, output, pending, trace, outcome, actor)
    if output.booking is not None:
        return _process_booking(crm, convo, lead, message, output, registry, property_id, trace, outcome)
    if output.requirements is not None and readiness(lead)['matching_ready']:
        _compose_recommendation(conn_factory, convo, lead, message, registry, policy, trace, outcome)
    return outcome


def _apply_requirements(crm, convo, lead, message, proposal, trace, actor):
    from app.crm_schemas import LeadUpdate
    provided = {name for name in ('preferred_localities', 'budget_min', 'budget_max', 'bedrooms',
                                  'purchase_timeframe') if getattr(proposal, name) is not None}
    if not provided:
        return
    with crm.connect() as conn:
        with conn.cursor() as cur:
            current = cur.execute('SELECT version FROM soloestate.leads WHERE id=%s', (lead['id'],)).fetchone()[0]
    internal = LeadUpdate(request_key='channel:requirements:' + str(message['id']), expected_version=current,
                          name=lead['name'], email=lead['email'], status=lead['status'], notes=lead['notes'],
                          **{name: getattr(proposal, name) for name in provided})
    saved = crm.save('leads', internal, lead['id'], actor=actor, freeze_conversation_id=convo['id'])
    lead.update(saved['record'])
    trace.append('requirements applied: %s' % sorted(provided))


def _process_confirm(crm, convo, lead, message, output, pending, trace, outcome, actor):
    from app import booking_requests
    from app.crm_schemas import BookingCommit, BookingConfirm
    if output.category != 'availability':
        return {'abort': 'confirmation turns use category availability until the booking commits'}
    if pending is None or str(output.confirm_request) != str(pending['id']):
        return {'abort': 'confirm_request does not match the live pending request of this conversation'}
    live = booking_requests.detail(crm, pending['id'])['record']
    if live['status'] == 'proposed':
        confirmed = booking_requests.confirm(
            crm, pending['id'],
            BookingConfirm(request_key='channel:confirm:' + str(message['id']),
                           expected_version=live['version']))
        trace.append('confirm: %s' % confirmed['record']['status'])
        live = confirmed['record']
        if not confirmed.get('confirmed'):
            _compose_exception(outcome, live, trace)
            return outcome
    elif live['status'] != 'approved':
        return {'abort': 'pending request is no longer confirmable (status %s)' % live['status']}
    committed = booking_requests.commit(
        crm, pending['id'],
        BookingCommit(request_key='channel:commit:' + str(message['id']),
                      expected_request_hash=live['request_hash']), actor=actor)
    if not committed.get('committed'):
        trace.append('commit refused: %s' % committed.get('message'))
        _compose_exception(outcome, committed['record'], trace)
        return outcome
    _compose_confirmation(crm, committed, trace, outcome)
    return outcome


def _process_booking(crm, convo, lead, message, output, registry, property_id, trace, outcome):
    from app import booking_requests
    from app.crm_schemas import BookingRequestCreate
    if output.category != 'availability':
        return {'abort': 'booking turns use category availability until the booking commits'}
    if property_id is None:
        resolved = [ref for ref, fact in registry.items() if fact['ftype'] == 'property']
        if len(resolved) != 1:
            return {'abort': 'booking needs a selected property; present the matches first'}
        property_id = UUID(registry[resolved[0]]['row_id'])
        outcome['select_property'] = property_id
    channel = 'whatsapp' if convo.get('channel') == 'whatsapp' else 'website'
    proposed = booking_requests.propose(crm, BookingRequestCreate(
        request_key='channel:propose:' + str(message['id']), lead_id=lead['id'], property_id=property_id,
        conversation_id=convo['id'], channel=channel,
        requested_start=output.booking.start, requested_end=output.booking.end))
    trace.append('propose: %s' % proposed['record']['status'])
    if proposed['record']['status'] == 'awaiting_owner':
        from psycopg.rows import dict_row
        with crm.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                live = cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s',
                                   (proposed['record']['id'],)).fetchone()
        _compose_exception(outcome, live, trace)
        return outcome
    _compose_confirm_question(crm, outcome, proposed, property_id, trace)
    return outcome


def _as_dt(value):
    # Service responses encode datetimes as ISO strings; raw rows keep them.
    return value if not isinstance(value, str) else datetime.fromisoformat(value)


def _window_fact(ref, request_id, start, end, rendered):
    start, end = _as_dt(start), _as_dt(end)
    return dict(ref=ref, ftype='window', row_id=str(request_id), version=None, rendered=rendered,
                shareable=True, starts_at=start.isoformat(), ends_at=end.isoformat())


def _compose_confirm_question(crm, outcome, proposed, property_id, trace):
    from psycopg.rows import dict_row
    record = proposed['record']
    start = record['customer_confirmed_start'] or record['requested_start']
    end = record['customer_confirmed_end'] or record['requested_end']
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            version = cur.execute('SELECT version FROM soloestate.properties WHERE id=%s',
                                  (property_id,)).fetchone()['version']
    outcome['extra_facts'] = {
        'f_window': _window_fact('f_window', record['id'], start, end, 'pending render'),
        'f_property': dict(ref='f_property', ftype='property', row_id=str(property_id), version=version,
                           rendered='pending render', shareable=True),
    }
    outcome['server_output'] = respond.TurnOutput(
        catalog_version=catalog.CATALOG_VERSION, category='availability',
        segments=[respond.Segment(question_id='Q_CONFIRM_WINDOW',
                                  args={'property': 'f_property', 'window': 'f_window'})])
    outcome['forced_category'] = 'availability'
    outcome['required_entry'] = 'Q_CONFIRM_WINDOW'
    trace.append('confirm question composed for request %s' % record['id'])


def _compose_confirmation(crm, committed, trace, outcome):
    from psycopg.rows import dict_row
    record = committed['record']
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            version = cur.execute('SELECT version FROM soloestate.properties WHERE id=%s',
                                  (record['property_id'],)).fetchone()['version']
            windows = cur.execute("""SELECT s.starts_at, s.ends_at FROM soloestate.bookings b
                JOIN soloestate.visit_slots s ON s.id=b.slot_id WHERE b.id=%s""",
                                  (committed['booking_id'],)).fetchone()
    outcome['extra_facts'] = {
        'f_window': _window_fact('f_window', record['id'], windows['starts_at'], windows['ends_at'],
                                 'pending render'),
        'f_property': dict(ref='f_property', ftype='property', row_id=str(record['property_id']),
                           version=version, rendered='pending render', shareable=True),
        'f_ref': dict(ref='f_ref', ftype='record', row_id=str(committed['booking_id']), version=None,
                      rendered=str(committed['booking_id'])[:8], shareable=True),
    }
    outcome['server_output'] = respond.TurnOutput(
        catalog_version=catalog.CATALOG_VERSION, category='booking_confirmed',
        segments=[respond.Segment(phrase_id='BOOK_CONFIRMED',
                                  args={'property': 'f_property', 'window': 'f_window', 'ref': 'f_ref'})])
    outcome['forced_category'] = 'booking_confirmed'
    outcome['required_entry'] = 'BOOK_CONFIRMED'
    trace.append('booked: %s' % committed['booking_id'])


def _compose_exception(outcome, live, trace):
    outcome['server_output'] = respond.TurnOutput(
        catalog_version=catalog.CATALOG_VERSION, category='availability',
        segments=[respond.Segment(phrase_id='BOOK_EXCEPTION')])
    outcome['forced_category'] = 'availability'
    outcome['required_entry'] = 'BOOK_EXCEPTION'
    outcome['escalate_conversation'] = True
    trace.append('exception: %s (%s)' % (live['id'], live['status']))


def _compose_recommendation(conn_factory, convo, lead, message, registry, policy, trace, outcome):
    """Deterministic recommendation when this turn completes requirements."""
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            fresh = respond.build_registry(cur, convo, lead, message['body'], policy,
                                           datetime.now(timezone.utc))
    registry.update(fresh)
    if any(fact['ftype'] == 'match_list' for fact in registry.values()):
        ref = next(ref for ref, fact in registry.items() if fact['ftype'] == 'match_list')
        outcome['server_output'] = respond.TurnOutput(
            catalog_version=catalog.CATALOG_VERSION, category='recommendation',
            segments=[respond.Segment(phrase_id='REC_MATCHES', args={'matches': ref})])
        outcome['forced_category'] = 'recommendation'
        outcome['required_entry'] = 'REC_MATCHES'
        trace.append('requirements completed; matches composed')
    else:
        outcome['server_output'] = respond.TurnOutput(
            catalog_version=catalog.CATALOG_VERSION, category='recommendation',
            segments=[respond.Segment(phrase_id='REC_NONE')])
        outcome['forced_category'] = 'recommendation'
        outcome['required_entry'] = 'REC_NONE'
        trace.append('requirements completed; no matches')


def _stage_delivery(cur, live, lead, message, body, required, grounding, fact_deps=None):
    """Stage the customer-visible text for durable delivery in-txn.

    The reply row and its outbox row commit atomically: no validated text
    without a delivery record, and no delivery record without validated
    text. Blocks persist owner-visible `skipped` rows with reasons.
    """
    from app import dispatch
    from app.identity_links import normalize_sender
    channel = live.get('channel')
    if channel != 'whatsapp':
        return {'staged': False, 'reason': 'no_delivery_channel'}
    evidence = {}
    if message.get('request_key'):
        op = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                         (message['request_key'],)).fetchone()
        if op is not None and isinstance((op['response'] or {}).get('inbound_evidence'), dict):
            evidence = op['response']['inbound_evidence']
    destination = normalize_sender(channel, evidence.get('provider_ref'))
    return dispatch.stage_in_txn(
        cur, request_key=dispatch.stable_key(channel, 'reply', str(message['id'])),
        channel=channel, purpose='reply', source_id=str(message['id']), lead_id=lead['id'],
        conversation_id=live['id'], link_id=evidence.get('link_id'),
        destination=destination or 'unresolved', category=dispatch.CATEGORY_MAP[required],
        payload={'body': body, 'catalog_version': catalog.CATALOG_VERSION,
                 'message_id': str(message['id']), 'grounding': grounding},
        content_ref='reply:' + str(message['id']), catalog_version=catalog.CATALOG_VERSION,
        fact_deps=fact_deps)


def _commit_degraded(crm, conn_factory, convo, lead, message, reply_key, trace, actor):
    """Degraded turn: no model call, server-composed clarification.

    Pre-tail facts are treated as unknown: the turn clarifies instead of
    asserting, processes no proposals, and persists the degraded flag on
    its record with an owner-visible activity entry.
    """
    selected = respond.TurnOutput(
        catalog_version=catalog.CATALOG_VERSION, category='clarification',
        segments=[respond.Segment(question_id='Q_CLARIFY')])
    return _commit_reply(crm, conn_factory, convo, lead, message, selected, selected, {}, {},
                         None, reply_key, trace, 'clarification', 'Q_CLARIFY', actor, degraded=True)


def _commit_reply(crm, conn_factory, convo, lead, message, selected, output, registry,
                  outcome, result, reply_key, trace, required, entry, actor, degraded=False):
    from app.conversation import log_activity
    from app.qualification import property_matches
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            live = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (convo['id'],)).fetchone()
            frozen = _frozen_reason(live, lead)
            if frozen is not None:
                raise _TurnAbort(frozen)
            prior = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                                (reply_key,)).fetchone()
            if prior is not None:
                saved = dict(prior['response'].get('reply', {}))
                saved['replay'] = True
                return saved
            # Linearization: facts re-read under the conversation lock; any
            # drift downgrades to the fixed fallback inside this same txn.
            rendered, errors = respond.validate_selection(
                cur, selected, registry, required_category=required, required_entry=entry,
                allowed_actions=outcome.get('allowed_actions', {}),
                conversation=live, message_text=message['body'])
            if errors:
                trace.append('commit revalidation: ' + '; '.join(errors))
                return _persist_fallback(cur, live, lead, message, reply_key, trace,
                                         {'selected': selected.model_dump(mode='json')},
                                         [result.text if result else None], actor)
            if outcome.get('select_property') is not None:
                matches = property_matches(cur, lead, 20)
                if str(outcome['select_property']) not in {str(match.id) for match in matches.matches}:
                    trace.append('commit revalidation: property selection left the current matches')
                    return _persist_fallback(cur, live, lead, message, reply_key, trace, {},
                                             [result.text if result else None], actor)
                cur.execute('UPDATE soloestate.conversations SET property_id=%s WHERE id=%s',
                            (outcome['select_property'], convo['id']))
            after = live
            if outcome.get('escalate_conversation'):
                after = cur.execute("UPDATE soloestate.conversations SET status='pending_owner' WHERE id=%s RETURNING *",
                                    (convo['id'],)).fetchone()
                log_activity(cur, live, after, 'Booking needs owner approval', reply_key, actor)
            try:
                body = respond.render_selection(selected, registry, rendered, outcome.get('allowed_actions', {}))
            except respond.InvalidSelection as exc:
                trace.append('commit render: %s' % exc)
                return _persist_fallback(cur, after, lead, message, reply_key, trace, {},
                                         [result.text if result else None], actor)
            # Contract §16: validated ⇒ citations empty; FAQ claims keep
            # grounded with B3-shaped citations (faq_id, version, quote)
            # so owner and visitor views parse them without weakening.
            citations = []
            stored_grounding = 'validated'
            if required == 'faq':
                stored_grounding = 'grounded'
                pins = []
                for segment in selected.segments:
                    for ref in (segment.args or {}).values():
                        fact = registry.get(ref)
                        if fact is not None and fact['ftype'] == 'faq_quotes':
                            pins.extend(fact['pins'])
                if pins:
                    answers = cur.execute(
                        'SELECT faq_id, version, answer FROM soloestate.faq_versions WHERE (faq_id, version) IN '
                        + '(' + ','.join(['(%s,%s)'] * len(pins)) + ')',
                        [item for pin in pins for item in (pin['faq_id'], pin['version'])]).fetchall()
                    by_key = {(str(row['faq_id']), row['version']): row['answer'] for row in answers}
                    citations = [{'faq_id': pin['faq_id'], 'version': pin['version'],
                                  'quote': by_key.get((pin['faq_id'], pin['version']), '')[:2000]}
                                 for pin in pins]
            seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                              'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
            row = cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,grounding,
                citations,model_name,response_id,request_key) VALUES (%s,%s,'assistant',%s,%s,%s,%s,%s,%s)
                RETURNING *""",
                              (convo['id'], seq, body, stored_grounding, Jsonb(citations),
                               result.model if result else None, result.response_id if result else None,
                               reply_key)).fetchone()
            fact_deps = respond.fact_dependencies(cur, selected, registry)
            staged = _stage_delivery(cur, live, lead, message, body, required, stored_grounding,
                                     fact_deps=fact_deps)
            staged_record = staged.get('record') or {}
            delivery = {'outbox_id': str(staged_record.get('id')) if staged_record.get('id') else None,
                        'status': staged_record.get('status', staged.get('reason')),
                        'skipped': staged.get('skipped')}
            trace.append('delivery: %s' % delivery['status'])
            if degraded:
                log_activity(cur, live, live, 'Turn ran on degraded context; clarified instead of asserting',
                             reply_key, actor)
            reply = {'replied': True, 'message_id': str(row['id']), 'payload': body,
                     'grounding': stored_grounding, 'trace': trace, 'delivery': delivery,
                     'context_degraded': degraded or None}
            composed = degraded or selected is not output
            from app.owner import payload_hash
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (reply_key, 'channel:reply',
                         payload_hash({'action': 'channel:reply', 'message_id': str(message['id'])}),
                         actor, convo['id'], Jsonb({
                             'reply': reply,
                             'raw_outputs': [{'text': result.text if result else None, 'validated': False}],
                             'selected': output.model_dump(mode='json'),
                             'server_composed': composed,
                             'server_selected': selected.model_dump(mode='json') if composed else None,
                             'rendered': body, 'trace': trace, 'delivery': delivery,
                             'context_degraded': degraded})))
            return reply


def _persist_fallback(cur, live, lead, message, reply_key, trace, evidence, raws=None, actor='channel'):
    from app.conversation import log_activity
    from psycopg.types.json import Jsonb
    after = cur.execute("UPDATE soloestate.conversations SET status='pending_owner' WHERE id=%s RETURNING *",
                        (live['id'],)).fetchone()
    log_activity(cur, live, after, 'Assistant answer needs owner review', reply_key, actor)
    seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                      'WHERE conversation_id=%s', (live['id'],)).fetchone()['next']
    row = cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,grounding,
        unsupported_reason,request_key) VALUES (%s,%s,'assistant',%s,'unsupported',%s,%s) RETURNING *""",
                      (live['id'], seq, catalog.FALLBACK_TEXT, 'invalid_response', reply_key)).fetchone()
    # The fallback body is fixed server text asserting no business
    # facts: an explicitly validated fact-free staging (empty pin list).
    staged = _stage_delivery(cur, live, lead, message, catalog.FALLBACK_TEXT, 'fallback', 'unsupported',
                             fact_deps=[])
    staged_record = staged.get('record') or {}
    delivery = {'outbox_id': str(staged_record.get('id')) if staged_record.get('id') else None,
                'status': staged_record.get('status', staged.get('reason')),
                'skipped': staged.get('skipped')}
    trace.append('delivery: %s' % delivery['status'])
    reply = {'replied': True, 'message_id': str(row['id']), 'payload': catalog.FALLBACK_TEXT,
             'grounding': 'unsupported', 'trace': trace, 'delivery': delivery}
    from app.owner import payload_hash
    cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                'VALUES (%s,%s,%s,%s,%s,%s)',
                (reply_key, 'channel:reply',
                 payload_hash({'action': 'channel:reply', 'message_id': str(message['id'])}),
                 actor, live['id'], Jsonb({'reply': reply, 'trace': trace, 'evidence': evidence,
                                               'delivery': delivery,
                                               'raw_outputs': [{'text': text, 'validated': False}
                                                               for text in (raws or [])]})))
    return reply


def _commit_fallback(crm, conn_factory, convo, lead, message, reply_key, trace, raws, actor):
    from psycopg.rows import dict_row
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            live = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (convo['id'],)).fetchone()
            frozen = _frozen_reason(live, lead)
            if frozen is not None:
                raise _TurnAbort(frozen)
            prior = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                                (reply_key,)).fetchone()
            if prior is not None:
                saved = dict(prior['response'].get('reply', {}))
                saved['replay'] = True
                return saved
            return _persist_fallback(cur, live, lead, message, reply_key, trace, {}, raws, actor)


def _escalate_sensitive(conn_factory, convo, lead, message, markers, reply_key, actor):
    from app.conversation import log_activity
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            live = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (convo['id'],)).fetchone()
            frozen = _frozen_reason(live, lead)
            if frozen is not None:
                raise _TurnAbort(frozen)
            prior = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                                (reply_key,)).fetchone()
            if prior is not None:
                saved = dict(prior['response'].get('reply', {}))
                saved['replay'] = True
                return saved
            after = cur.execute("UPDATE soloestate.conversations SET status='pending_owner' WHERE id=%s RETURNING *",
                                (convo['id'],)).fetchone()
            log_activity(cur, live, after, 'Sensitive request needs owner review', reply_key, actor)
            reply = {'replied': False, 'reason': 'sensitive_escalated', 'markers': markers}
            from app.owner import payload_hash
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (reply_key, 'channel:escalate',
                         payload_hash({'action': 'channel:escalate', 'message_id': str(message['id'])}),
                         actor, convo['id'], Jsonb({'reply': reply, 'markers': markers})))
            return reply
