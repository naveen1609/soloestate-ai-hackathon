"""Closed-repertoire response pipeline (Phase D2, §5).

The model selects catalog ids and fact references; the backend renders
every token. Validation re-reads every referenced row on current data:
stale, unshareable, or mutated values reject the turn. One regeneration
with machine feedback is allowed, then the fixed fallback + escalation.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app import catalog
from app.qualification import Timeframe

MAX_SEGMENTS = 6
MAX_RENDERED_CHARS = 4000
QUOTE_CHARS = 1200


class InvalidSelection(Exception):
    pass


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phrase_id: str | None = None
    question_id: str | None = None
    variant: int = 0
    args: dict[str, str] = Field(default_factory=dict)
    fact_only: list[str] | None = None
    action_ref: UUID | None = None

    @model_validator(mode='after')
    def one_shape(self):
        shapes = [self.phrase_id is not None, self.question_id is not None,
                  self.fact_only is not None, self.action_ref is not None]
        if sum(shapes) != 1:
            raise ValueError('Each segment selects exactly one shape')
        if self.fact_only is not None and (not self.fact_only or len(self.fact_only) > 5):
            raise ValueError('fact_only carries 1-5 fact references')
        if self.variant < 0 or self.variant > 3:
            raise ValueError('variant out of range')
        return self


class RequirementsProposal(BaseModel):
    """Model-proposed requirement fields; backend-validated before apply.

    Mirrors the ChatRequirements field rules so channel extraction obeys
    the same bounds as visitor capture. Only provided fields are applied.
    """
    model_config = ConfigDict(extra='forbid')
    preferred_localities: list[str] | None = Field(default=None, min_length=1, max_length=20)
    budget_min: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    budget_max: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    bedrooms: int | None = Field(default=None, ge=1, le=30, strict=True)
    purchase_timeframe: Timeframe | None = None

    @field_validator('preferred_localities')
    @classmethod
    def localities(cls, value):
        if value is None:
            return None
        cleaned, seen = [], set()
        for item in value:
            normalized = item.strip() if isinstance(item, str) else ''
            if normalized.lower() not in seen:
                cleaned.append(normalized)
                seen.add(normalized.lower())
        if any(not item or len(item) > 120 for item in cleaned):
            raise ValueError('Localities must be nonempty and at most 120 characters')
        return cleaned

    @model_validator(mode='after')
    def budgets(self):
        if self.budget_max is not None and self.budget_min is not None and self.budget_max < self.budget_min:
            raise ValueError('Maximum budget must cover minimum budget')
        return self


class BookingProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode='after')
    def window(self):
        if self.end <= self.start:
            raise ValueError('Requested window must be positive')
        return self


class TurnOutput(BaseModel):
    """Strict turn schema. Any `text` key or unknown field rejects the turn."""
    model_config = ConfigDict(extra='forbid')
    catalog_version: str
    category: str
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    requirements: RequirementsProposal | None = None
    booking: BookingProposal | None = None
    confirm_request: UUID | None = None
    select_property: str | None = None


def parse_output(text):
    try:
        return TurnOutput.model_validate_json(text)
    except ValueError as exc:
        raise InvalidSelection('Turn output failed strict validation: %s' % exc) from None


def render_window(start, end, tzname):
    zone = ZoneInfo(tzname)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    return '%s %d %s, %s-%s %s' % (
        local_start.strftime('%a'), local_start.day, local_start.strftime('%b'),
        local_start.strftime('%H:%M'), local_end.strftime('%H:%M'), local_start.strftime('%Z'))


def render_property(row):
    return '%s (%s), price %s, %s BHK' % (row['title'], row['locality'], row['price'], row['bedrooms'])


def two_day_window(policy, now):
    """Next-two-days availability window per §13, as UTC instants."""
    zone = ZoneInfo(policy['timezone'])
    local = now.astimezone(zone)
    day_after = (local + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    since = now + timedelta(hours=policy['notice_hours'])
    return since, day_after.astimezone(timezone.utc)


def _faq_overlap(text, row):
    from app.faq import tokens
    return len(tokens(text) & tokens(row['question'] + ' ' + row['answer']))


def build_registry(cur, conversation, lead, message_text, policy, now):
    """Fresh-read fact registry for one turn: refs f1..fn, server renderings.

    Every fact pins the row identity + version it was read from; the
    commit-time validator re-reads each row and rejects drift. Only
    shareable rows (active properties/FAQs, live slots) enter the registry.
    """
    from app.faq import retrieve
    from app.policies import availability_windows
    from app.qualification import property_matches
    registry = {}
    counter = [0]

    def add(ftype, row_id, version, rendered, extra=None):
        counter[0] += 1
        ref = 'f%d' % counter[0]
        fact = dict(ref=ref, ftype=ftype, row_id=str(row_id), version=version,
                    rendered=rendered, shareable=True)
        if extra:
            fact.update(extra)
        registry[ref] = fact
        return ref

    matches = property_matches(cur, lead, 5)
    if matches.matching_ready and matches.matches:
        member_refs = []
        for match in matches.matches:
            row = cur.execute('SELECT id,title,locality,price,bedrooms,version FROM soloestate.properties '
                              'WHERE id=%s AND active', (match.id,)).fetchone()
            if row is None:
                continue
            member_refs.append(add('property', row['id'], row['version'], render_property(row)))
        if member_refs:
            lines = ['%d. %s' % (number, registry[ref]['rendered'])
                     for number, ref in enumerate(member_refs, 1)]
            add('match_list', 'matches', None, '\n'.join(lines), {'members': member_refs})
    retrieved = retrieve(cur, conversation, message_text, 5)
    supported = [row for row in retrieved if _faq_overlap(message_text, row) >= 1][:3]
    if supported:
        quotes = []
        pins = []
        for row in supported:
            live = cur.execute('SELECT current_version,status FROM soloestate.faqs WHERE id=%s', (row['faq_id'],)).fetchone()
            if live is None or live['status'] != 'active' or live['current_version'] != row['version']:
                continue
            quote = row['answer'][:QUOTE_CHARS]
            quotes.append('[FAQ %s v%s] %s' % (row['faq_id'], row['version'], quote))
            pins.append({'faq_id': str(row['faq_id']), 'version': row['version']})
        if quotes:
            add('faq_quotes', 'faqs', None, '\n\n'.join(quotes), {'pins': pins})
    since, until = two_day_window(policy, now)
    if since < until:
        windows = availability_windows(cur, since, until, policy=dict(policy), now=now)[:6]
        slot_refs = []
        for window in windows:
            slot_refs.append(add('slot', window['id'], None,
                                 render_window(window['starts_at'], window['ends_at'], policy['timezone']),
                                 {'starts_at': window['starts_at'].isoformat(),
                                  'ends_at': window['ends_at'].isoformat(),
                                  'resource_id': str(window['resource_id'])}))
        if slot_refs:
            lines = ['%d. %s' % (number, registry[ref]['rendered'])
                     for number, ref in enumerate(slot_refs, 1)]
            add('slot_list', 'slots', None, '\n'.join(lines), {'members': slot_refs})
    return registry


def _recheck_fact(cur, fact, registry, conversation, message_text):
    """Re-read the pinned row; returns (rendered, error). Rendering always
    comes from the live row: the model can never mutate a served value."""
    ftype = fact['ftype']
    if ftype == 'property':
        row = cur.execute('SELECT id,title,locality,price,bedrooms,version,active FROM soloestate.properties '
                          'WHERE id=%s', (fact['row_id'],)).fetchone()
        if row is None or not row['active']:
            return None, 'fact %s is no longer available' % fact['ref']
        if row['version'] != fact['version']:
            return None, 'fact %s changed since the turn began' % fact['ref']
        return render_property(row), None
    if ftype == 'match_list':
        parts = []
        for number, ref in enumerate(fact['members'], 1):
            rendered, error = _recheck_fact(cur, registry[ref], registry, conversation, message_text)
            if error:
                return None, error
            parts.append('%d. %s' % (number, rendered))
        return '\n'.join(parts), None
    if ftype == 'faq_quotes':
        from app.conversation import Citation, DraftCandidate, validate_citations
        from app.faq import retrieve
        retrieved = retrieve(cur, conversation, message_text, 5)
        quotes = []
        answers = {}
        for pin in fact['pins']:
            stored = cur.execute('SELECT answer FROM soloestate.faq_versions WHERE faq_id=%s AND version=%s',
                                 (pin['faq_id'], pin['version'])).fetchone()
            if stored is None:
                return None, 'fact %s cites a missing FAQ version' % fact['ref']
            answers[pin['faq_id']] = stored['answer']
            quotes.append('[FAQ %s v%s] %s' % (pin['faq_id'], pin['version'], stored['answer'][:QUOTE_CHARS]))
        candidate = DraftCandidate(answer='server-side quote assembly', confidence='high', citations=[
            Citation(faq_id=pin['faq_id'], version=pin['version'], quote=answers[pin['faq_id']][:QUOTE_CHARS])
            for pin in fact['pins']])
        reason = validate_citations(cur, retrieved, candidate)
        if reason is not None:
            return None, 'fact %s failed FAQ validation: %s' % (fact['ref'], reason)
        return '\n\n'.join(quotes), None
    if ftype == 'slot':
        row = cur.execute('SELECT id,resource_id,starts_at,ends_at,enabled FROM soloestate.visit_slots '
                          'WHERE id=%s', (fact['row_id'],)).fetchone()
        if row is None or not row['enabled']:
            return None, 'fact %s is no longer available' % fact['ref']
        if row['starts_at'].isoformat() != fact['starts_at'] or row['ends_at'].isoformat() != fact['ends_at']:
            return None, 'fact %s changed since the turn began' % fact['ref']
        policy = cur.execute("SELECT timezone FROM soloestate.business_policies WHERE id='default'").fetchone()
        return render_window(row['starts_at'], row['ends_at'], policy['timezone']), None
    if ftype == 'slot_list':
        parts = []
        for number, ref in enumerate(fact['members'], 1):
            rendered, error = _recheck_fact(cur, registry[ref], registry, conversation, message_text)
            if error:
                return None, error
            parts.append('%d. %s' % (number, rendered))
        return '\n'.join(parts), None
    if ftype == 'window':
        row = cur.execute('SELECT status, requested_start, requested_end, customer_confirmed_start, '
                          'customer_confirmed_end, owner_approved_start, owner_approved_end '
                          'FROM soloestate.booking_requests WHERE id=%s', (fact['row_id'],)).fetchone()
        if row is None or row['status'] not in ('proposed', 'approved', 'booked', 'awaiting_owner'):
            return None, 'fact %s no longer references a live request' % fact['ref']
        start = row['customer_confirmed_start'] or row['owner_approved_start'] or row['requested_start']
        end = row['customer_confirmed_end'] or row['owner_approved_end'] or row['requested_end']
        if start.isoformat() != fact['starts_at'] or end.isoformat() != fact['ends_at']:
            return None, 'fact %s changed since the turn began' % fact['ref']
        policy = cur.execute("SELECT timezone FROM soloestate.business_policies WHERE id='default'").fetchone()
        return render_window(start, end, policy['timezone']), None
    if ftype == 'record':
        return fact['rendered'], None
    return None, 'fact %s has an unknown type' % fact['ref']


def fact_dependencies(cur, selected, registry):
    """Persistable pins for the facts a validated selection renders.

    Each dependency records the entity id plus the exact values the body
    states (never the mutable row version: unrelated edits bump it), so
    the submission boundary can revalidate precisely what the customer
    was told and nothing more. List facts expand to their members;
    record facts pin the booking whose confirmation the body asserts.
    """
    deps = []
    seen = set()

    def add(dep):
        key = (dep['type'], dep['id'])
        if key not in seen:
            seen.add(key)
            deps.append(dep)

    def expand(fact):
        ftype = fact['ftype']
        if ftype == 'property':
            row = cur.execute('SELECT title,locality,price,bedrooms FROM soloestate.properties WHERE id=%s',
                              (fact['row_id'],)).fetchone()
            if row is not None:
                add(dict(type='property', id=fact['row_id'], title=row['title'],
                         locality=row['locality'], price=str(row['price']),
                         bedrooms=int(row['bedrooms'])))
        elif ftype in ('match_list', 'slot_list'):
            for ref in fact['members']:
                member = registry.get(ref)
                if member is not None:
                    expand(member)
        elif ftype == 'faq_quotes':
            for pin in fact['pins']:
                add(dict(type='faq', id=pin['faq_id'], version=int(pin['version'])))
        elif ftype == 'slot':
            add(dict(type='slot', id=fact['row_id'], starts_at=fact['starts_at'],
                     ends_at=fact['ends_at']))
        elif ftype == 'window':
            add(dict(type='window', id=fact['row_id'], starts_at=fact['starts_at'],
                     ends_at=fact['ends_at']))
        elif ftype == 'record':
            add(dict(type='booking', id=fact['row_id']))

    for segment in selected.segments:
        refs = list((segment.args or {}).values())
        if segment.fact_only:
            refs.extend(segment.fact_only)
        for ref in refs:
            fact = registry.get(ref)
            if fact is not None:
                expand(fact)
    return deps


def _check_ref(cur, ref, registry, expected_type, conversation, message_text, rendered_out, errors):
    fact = registry.get(ref)
    if fact is None:
        errors.append('unknown fact reference %s' % ref)
        return
    if fact['ftype'] != expected_type:
        errors.append('fact %s has type %s, slot needs %s' % (ref, fact['ftype'], expected_type))
        return
    rendered, error = _recheck_fact(cur, fact, registry, conversation, message_text)
    if error:
        errors.append(error)
        return
    rendered_out[ref] = rendered


def validate_selection(cur, output, registry, *, required_category, required_entry,
                       allowed_actions, conversation, message_text):
    """Backend category recompute + full structural validation.

    Returns (rendered_facts, errors). The turn's required category is
    computed by the backend from server state, never trusted from the
    model label; any mismatch rejects the turn for regeneration.
    """
    errors = []
    rendered = {}
    if output.catalog_version != catalog.CATALOG_VERSION:
        errors.append('catalog version %s is not current' % output.catalog_version)
        return rendered, errors
    if output.category not in catalog.AUTO_CATEGORIES:
        errors.append('category %s is not auto-sendable' % output.category)
        return rendered, errors
    if output.category != required_category:
        errors.append('category %s does not match required %s' % (output.category, required_category))
        return rendered, errors
    seen_entries = []
    for index, segment in enumerate(output.segments):
        if segment.action_ref is not None:
            if str(segment.action_ref) not in allowed_actions:
                errors.append('segment %d: action_ref is not a record of this turn' % index)
                continue
            _check_ref(cur, allowed_actions[str(segment.action_ref)], registry, 'record',
                       conversation, message_text, rendered, errors)
            continue
        if segment.fact_only is not None:
            for ref in segment.fact_only:
                fact = registry.get(ref)
                if fact is None:
                    errors.append('segment %d: unknown fact reference %s' % (index, ref))
                    continue
                if fact['ftype'] not in ('property', 'slot', 'record'):
                    errors.append('segment %d: fact %s cannot stand alone' % (index, ref))
                    continue
                _check_ref(cur, ref, registry, fact['ftype'], conversation, message_text, rendered, errors)
            continue
        entry_id = segment.phrase_id or segment.question_id
        entry = catalog.entry(entry_id)
        if entry is None:
            errors.append('segment %d: unknown catalog id %s' % (index, entry_id))
            continue
        if (segment.phrase_id is not None and entry['kind'] != 'phrase') or \
                (segment.question_id is not None and entry['kind'] != 'question'):
            errors.append('segment %d: %s is a %s, cited as the other kind' % (index, entry_id, entry['kind']))
            continue
        if entry['category'] != required_category and entry['category'] != catalog.TRANSITIONAL:
            if not (required_category == 'faq' and entry_id.startswith('ACK_')):
                errors.append('segment %d: %s belongs to %s, turn requires %s'
                              % (index, entry_id, entry['category'], required_category))
                continue
        if catalog.render_text(entry_id, segment.variant) is None:
            errors.append('segment %d: variant %d does not exist for %s' % (index, segment.variant, entry_id))
            continue
        if set(segment.args) != set(entry['slots']):
            errors.append('segment %d: %s needs slots %s' % (index, entry_id, sorted(entry['slots'])))
            continue
        for slot, ref in segment.args.items():
            _check_ref(cur, ref, registry, entry['slots'][slot], conversation, message_text, rendered, errors)
        seen_entries.append(entry_id)
    if required_entry is not None and required_entry not in seen_entries:
        errors.append('turn must include catalog entry %s' % required_entry)
    if required_category == 'faq':
        core = [entry_id for entry_id in seen_entries if catalog.entry(entry_id)['category'] == 'faq']
        if core != ['FAQ_ANSWER']:
            errors.append('faq turns render validated quotes only')
        elif seen_entries == ['FAQ_ANSWER']:
            pass
        elif len(seen_entries) == 2 and seen_entries[0].startswith('ACK_') \
                and seen_entries[1] == 'FAQ_ANSWER':
            pass
        else:
            errors.append('faq turns allow at most one leading catalog ACK prefix')
    return rendered, errors


def render_selection(output, registry, rendered_facts, allowed_actions):
    """Assemble catalog strings + canonical fact renderings, fixed order."""
    parts = []
    for segment in output.segments:
        if segment.action_ref is not None:
            parts.append(rendered_facts[allowed_actions[str(segment.action_ref)]])
            continue
        if segment.fact_only is not None:
            parts.append('\n'.join(rendered_facts[ref] for ref in segment.fact_only))
            continue
        entry_id = segment.phrase_id or segment.question_id
        template = catalog.render_text(entry_id, segment.variant)
        parts.append(template.format(**{slot: rendered_facts[ref] for slot, ref in segment.args.items()}))
    body = '\n\n'.join(parts)
    if len(body) > MAX_RENDERED_CHARS:
        raise InvalidSelection('Rendered payload exceeds the %d-character bound' % MAX_RENDERED_CHARS)
    return body


def turn_prompt(*, conversation, lead, context, registry, message_text, supportable,
                required_entry, policy, now, pending_request=None, extra_feedback=None):
    """Model prompt: catalog choice over server facts. Retrieved text,
    summaries, descriptions and the customer message are untrusted and
    wrapped in delimited blocks; they cannot add catalog ids (the
    strict schema forbids them) or alter any gate outcome. The backend
    recomputes supportability after proposals and rejects any choice
    outside the recomputed set."""
    lines = [
        'You are the SoloEstate receptionist. Select a response using ONLY the catalog ids and fact refs below.',
        'Business timezone: %s. Current business-local time: %s.' % (
            policy['timezone'], now.astimezone(ZoneInfo(policy['timezone'])).strftime('%a %d %b %Y %H:%M %Z')),
        'Supportable categories (pick exactly one): %s.' % ', '.join(sorted(supportable)),
    ]
    if required_entry is not None:
        lines.append('This turn MUST include catalog entry %s.' % required_entry)
    lines.append('Catalog (id | kind | category | slots | text):')
    for entry_id, entry in catalog.ENTRIES.items():
        if entry['category'] not in supportable and entry['category'] != catalog.TRANSITIONAL \
                and not entry_id.startswith('ACK_'):
            continue
        slots = ', '.join('%s:%s' % item for item in sorted(entry['slots'].items())) or 'none'
        lines.append('- %s | %s | %s | %s | %s' % (entry_id, entry['kind'], entry['category'], slots, entry['text']))
    lines.append('Facts (ref | type | rendered):')
    if registry:
        for ref in sorted(registry):
            lines.append('- %s | %s | %s' % (ref, registry[ref]['ftype'], registry[ref]['rendered'][:500]))
    else:
        lines.append('(no facts available this turn)')
    if pending_request is not None:
        lines.append('Pending booking request %s: %s. If the customer confirms it, echo its id in confirm_request; '
                     'never invent another id.' % (pending_request['id'], pending_request['rendered']))
    lines.append('Return one JSON object, no Markdown, no `text` key anywhere:')
    lines.append('{"catalog_version": "%s", "category": "<one of the supportable>", "segments": ['
                 '{"phrase_id": "<id>", "variant": 0, "args": {"<slot>": "<fact ref>"}} | '
                 '{"question_id": "<id>", ...} | {"fact_only": ["<ref>"]} | {"action_ref": "<uuid>"}]'
                 '%s%s%s%s}' % (
                     catalog.CATALOG_VERSION,
                     ', "requirements": {"preferred_localities": [...], "budget_min": "12000000", '
                     '"budget_max": "18000000", "bedrooms": 2, "purchase_timeframe": '
                     '"within_3_months|three_to_six_months|later|no_fixed_deadline"}',
                     ', "booking": {"start": "2026-11-05T11:00:00+05:30", "end": "2026-11-05T11:30:00+05:30"}',
                     ', "confirm_request": "<pending id>"', ', "select_property": "<property fact ref>"'))
    lines.append('Include requirements only for values the customer stated. Include booking only for an explicit '
                 'visit-time request, in business-local ISO with offset. Otherwise omit those keys.')
    if context['summary']:
        lines.append('--- untrusted prior summary (may be stale; CRM is the source of truth) ---')
        lines.append(context['summary']['body'][:2000])
    lines.append('--- untrusted recent messages ---')
    for row in context['messages']:
        lines.append('%s: %s' % (row['role'], (row['body'] or '')[:1000]))
    lines.append('--- untrusted current customer message ---')
    lines.append(message_text[:2000])
    if extra_feedback is not None:
        lines.append('--- machine feedback: your previous output was rejected ---')
        lines.append(extra_feedback[:2000])
    return '\n'.join(lines)
