"""Business policies, shared availability evaluation, slot generation, send-policy helpers.

D1 only: pure evaluation plus owner-managed transactional services. No provider calls,
no scheduling, no autonomous sending. Policy gates apply to booking-request flows and
gated availability reads; direct owner/tool booking writes keep integrity-only checks.
"""
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

OWNER_RESOURCE_ID = UUID('00000000-0000-4000-8000-000000000001')

ROUTINE_CATEGORIES = frozenset({
    'welcome', 'acknowledgement', 'qualification', 'recommendation', 'faq_answer',
    'clarification', 'availability', 'booking_confirmation', 'reminder', 'followup_routine'})
SENSITIVE_CATEGORIES = frozenset({
    'negotiation', 'discount', 'financial', 'legal', 'commitment', 'exception'})
MESSAGE_CATEGORIES = ROUTINE_CATEGORIES | SENSITIVE_CATEGORIES

DAY_CAP_DEFAULT = 25
COOLDOWN_HOURS_DEFAULT = 24
THREAD_HOURLY_DEFAULT = 3
GENERATION_RANGE_DAYS = 92


def validate_categories(categories):
    from app.crm import CRMError
    unknown = [c for c in categories if c not in MESSAGE_CATEGORIES]
    if unknown:
        raise CRMError('Unknown message categories: ' + ', '.join(sorted(unknown)) + '.', 422)


REPLY_POLICY_CHANNEL = 'whatsapp'


def validate_routine_categories(categories):
    """Immediate replies permit code-allowlisted routine categories only."""
    from app.crm import CRMError
    unknown = [c for c in categories if c not in MESSAGE_CATEGORIES]
    if unknown:
        raise CRMError('Unknown message categories: ' + ', '.join(sorted(unknown)) + '.', 422)
    sensitive = [c for c in categories if c in SENSITIVE_CATEGORIES]
    if sensitive:
        raise CRMError('Sensitive categories cannot be enabled for automatic replies: ' +
                       ', '.join(sorted(sensitive)) + '.', 422)


def get_reply_policy(crm):
    from app.crm import encode, found
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = found(cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s',
                                    (REPLY_POLICY_CHANNEL,)).fetchone())
    return encode({'record': dict(row)})


def update_reply_policy(crm, request):
    from app.crm import CRMError, encode, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.immediate_reply_policies WHERE id=%s FOR UPDATE',
                                       (REPLY_POLICY_CHANNEL,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Policy changed. Reload before editing.')
            provided = request.model_fields_set
            changes = {field: getattr(request, field) for field in ('enabled', 'categories')
                       if field in provided and getattr(request, field) is not None}
            if not changes:
                raise CRMError('No policy change requested.')
            if 'categories' in changes:
                validate_routine_categories(changes['categories'])
            merged = dict(before, **changes)
            if merged['enabled'] and not merged['categories']:
                raise CRMError('Enabling immediate replies requires at least one routine category.', 422)
            row = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=%s, categories=%s, version=version+1, '
                              "updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                              (merged['enabled'], merged['categories'], REPLY_POLICY_CHANNEL)).fetchone()
            return {'record': encode(row), 'before': encode(before), 'reason': request.reason.strip()}
    return crm.mutate('reply-policy:update', request, work)


def evaluate_window(policy, start, end, now, duration_min=None):
    """Pure policy evaluation of one requested window. Instants in, verdict out.

    Checks run in contract order: holiday date, weekly holiday, working day,
    working hours (end must fit), advance notice (UTC elapsed), duration range.
    """
    reasons = []
    tz = ZoneInfo(policy['timezone'])
    local_start, local_end = start.astimezone(tz), end.astimezone(tz)
    holidays = {d if isinstance(d, date) else date.fromisoformat(d) for d in (policy['holiday_dates'] or [])}
    if local_start.date() in holidays or local_end.date() in holidays:
        reasons.append('holiday_date')
    if local_start.weekday() == (policy['weekly_holiday'] + 6) % 7:
        reasons.append('weekly_holiday')
    if local_start.isoweekday() % 7 not in set(policy['working_days']):
        reasons.append('outside_working_day')
    if local_start.timetz().replace(tzinfo=None) < policy['hours_start'] or local_end.timetz().replace(tzinfo=None) > policy['hours_end']:
        reasons.append('outside_hours')
    if (start - now).total_seconds() < policy['notice_hours'] * 3600:
        reasons.append('short_notice')
    minutes = duration_min if duration_min is not None else (end - start).total_seconds() / 60
    if not policy['min_duration_min'] <= minutes <= policy['max_duration_min']:
        reasons.append('bad_duration')
    return {'passed': not reasons, 'reasons': reasons, 'policy_version': policy['version']}


def _buffered_conflict_clause(slot_alias='s'):
    return f"""NOT EXISTS (
        SELECT 1 FROM soloestate.bookings b
        JOIN soloestate.visit_slots bs ON bs.id=b.slot_id
        JOIN soloestate.approvals ba ON ba.id=b.approval_id
        WHERE b.status='confirmed' AND b.resource_id={slot_alias}.resource_id
          AND (bs.starts_at < {slot_alias}.ends_at + make_interval(mins=>%s)
               AND {slot_alias}.starts_at - make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,%s)) < bs.ends_at
           OR {slot_alias}.starts_at < bs.ends_at + make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,%s))
               AND bs.starts_at - make_interval(mins=>%s) < {slot_alias}.ends_at))"""


def availability_windows(cur, since, until, *, resource_id=None, policy=None, now=None,
                         apply_policy=True, limit=101):
    """One shared availability evaluator: enabled future slot rows fully inside the
    window, minus buffer-conflicting confirmed bookings, intersected with policy gates.
    Returns window rows ordered by (starts_at, id); caller detects truncation.
    """
    moment = now or datetime.now(timezone.utc)
    params = [moment, since, until]
    resource_clause = ''
    if resource_id is not None:
        resource_clause = 'AND s.resource_id=%s'
        params.append(resource_id)
    buf = policy['buffer_minutes'] if policy else 0
    rows = cur.execute(
        f"""SELECT s.id, s.resource_id, s.starts_at, s.ends_at FROM soloestate.visit_slots s
        WHERE s.enabled AND s.starts_at>=%s AND s.starts_at>=%s AND s.ends_at<=%s {resource_clause}
          AND {_buffered_conflict_clause()} ORDER BY s.starts_at, s.id LIMIT %s""",
        (*params, buf, buf, buf, buf, limit)).fetchall()
    if not apply_policy or policy is None:
        return rows
    return [row for row in rows if evaluate_window(policy, row['starts_at'], row['ends_at'], moment)['passed']]


def conflicts(cur, resource_id, start, end, buffer_min):
    """True when a confirmed booking for the resource conflicts (buffer-aware §6 predicate)."""
    from app.crm import scalar
    return scalar(cur.execute(
        """SELECT EXISTS (
        SELECT 1 FROM soloestate.bookings b
        JOIN soloestate.visit_slots bs ON bs.id=b.slot_id
        JOIN soloestate.approvals ba ON ba.id=b.approval_id
        WHERE b.status='confirmed' AND b.resource_id=%s
          AND (bs.starts_at < %s + make_interval(mins=>%s)
               AND %s - make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,%s)) < bs.ends_at
           OR %s < bs.ends_at + make_interval(mins=>COALESCE((ba.payload->>'buffer_min')::int,%s))
               AND bs.starts_at - make_interval(mins=>%s) < %s))""",
        (resource_id, end, buffer_min, start, buffer_min, start, buffer_min, buffer_min, end)).fetchone())


def site_capacity_ok(cur, property_id, start, end):
    """True when the property's concurrent-visit capacity (if set) has room."""
    from app.crm import scalar
    return scalar(cur.execute(
        """SELECT concurrent_visit_capacity IS NULL OR (
        SELECT count(*) FROM soloestate.bookings b JOIN soloestate.visit_slots bs ON bs.id=b.slot_id
        WHERE b.status='confirmed' AND b.property_id=%s
          AND bs.starts_at<%s AND %s<bs.ends_at) < concurrent_visit_capacity
        FROM soloestate.properties WHERE id=%s""", (property_id, end, start, property_id)).fetchone())


def get_policy(crm):
    from app.crm import encode
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT * FROM soloestate.business_policies WHERE id=%s', ('default',)).fetchone()
    return encode({'record': dict(row)})


def _validate_policy_fields(request, current):
    from app.crm import CRMError
    merged = dict(current)
    provided = request.model_fields_set
    for field in ('timezone', 'working_days', 'weekly_holiday', 'hours_start', 'hours_end', 'holiday_dates',
                  'notice_hours', 'default_duration_min', 'min_duration_min', 'max_duration_min', 'buffer_minutes'):
        value = getattr(request, field)
        if field in provided and value is not None:
            merged[field] = value
    try:
        ZoneInfo(merged['timezone'])
    except Exception:
        raise CRMError('Business timezone must be a valid IANA name.', 422)
    if not merged['working_days'] or any(d not in range(7) for d in merged['working_days']):
        raise CRMError('Working days must list weekdays 0 (Sunday) to 6 (Saturday).', 422)
    if merged['hours_end'] <= merged['hours_start']:
        raise CRMError('Working hours must end after they start.', 422)
    if not merged['min_duration_min'] <= merged['default_duration_min'] <= merged['max_duration_min']:
        raise CRMError('Default duration must fit within the allowed duration range.', 422)
    return {field: merged[field] for field in
            ('timezone', 'working_days', 'weekly_holiday', 'hours_start', 'hours_end', 'holiday_dates',
             'notice_hours', 'default_duration_min', 'min_duration_min', 'max_duration_min', 'buffer_minutes')}


def update_policy(crm, request):
    from app.crm import CRMError, encode, found
    from psycopg.rows import dict_row
    from psycopg import sql
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            current = found(cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default' FOR UPDATE").fetchone())
            if current['version'] != request.expected_version:
                raise CRMError('Policy changed. Reload before editing.')
            values = _validate_policy_fields(request, current)
            if all(values[field] == current[field] for field in values):
                raise CRMError('No policy change requested.')
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(field)) for field in values)
            row = cur.execute(sql.SQL('UPDATE soloestate.business_policies SET {} WHERE id=%s RETURNING *').format(assignments),
                              (*values.values(), 'default')).fetchone()
            return {'record': encode(row), 'reason': request.reason.strip()}
    return crm.mutate('policy:update', request, work)


def generate_slots(crm, request):
    from app.crm import CRMError, encode
    from psycopg.rows import dict_row
    from uuid import uuid4
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            resource = cur.execute('SELECT * FROM soloestate.resources WHERE id=%s FOR UPDATE',
                                   (request.resource_id or OWNER_RESOURCE_ID,)).fetchone()
            if resource is None or not resource['active']:
                raise CRMError('Slot generation requires an active resource.', 422)
            resource_id = resource['id']
            duration = request.duration_min or policy['default_duration_min']
            if not policy['min_duration_min'] <= duration <= policy['max_duration_min']:
                raise CRMError('Generation duration is outside the allowed range.', 422)
            if (request.end_date - request.start_date).days > GENERATION_RANGE_DAYS or request.end_date < request.start_date:
                raise CRMError(f'Generation covers at most {GENERATION_RANGE_DAYS} days.', 422)
            tz = ZoneInfo(policy['timezone'])
            step, created, skipped, slot_ids = timedelta(minutes=duration), 0, 0, []
            day = request.start_date
            while day <= request.end_date:
                if day not in set(policy['holiday_dates']) and day.isoweekday() % 7 in set(policy['working_days']) \
                        and day.weekday() != (policy['weekly_holiday'] + 6) % 7:
                    cursor = datetime.combine(day, policy['hours_start'], tzinfo=tz)
                    close = datetime.combine(day, policy['hours_end'], tzinfo=tz)
                    while cursor + step <= close:
                        window = (cursor, cursor + step)
                        exists = cur.execute(
                            """SELECT 1 FROM soloestate.visit_slots WHERE resource_id=%s
                               AND tstzrange(starts_at,ends_at) && tstzrange(%s,%s) LIMIT 1""",
                            (resource_id, *window)).fetchone()
                        if exists:
                            skipped += 1
                        else:
                            slot_id = uuid4()
                            cur.execute('INSERT INTO soloestate.visit_slots(id,starts_at,ends_at,resource_id) VALUES (%s,%s,%s,%s)',
                                        (slot_id, *window, resource_id))
                            slot_ids.append(slot_id)
                            created += 1
                        cursor += step
                day += timedelta(days=1)
            return {'record': {'id': resource_id, 'created': created, 'skipped_overlapping': skipped,
                               'slot_ids': encode(slot_ids)}}
    return crm.mutate('slot:generate', request, work)


def get_controls(crm):
    from app.crm import encode
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT * FROM soloestate.automation_controls WHERE id=%s', ('default',)).fetchone()
    return encode({'record': dict(row)})


def set_stop(crm, request):
    from app.crm import encode
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("UPDATE soloestate.automation_controls SET emergency_stop=%s, updated_at=clock_timestamp() "
                              "WHERE id='default' RETURNING *", (request.stopped,)).fetchone()
            return {'record': encode(row), 'reason': request.reason.strip()}
    return crm.mutate('automation-controls:set', request, work)


def create_automation_policy(crm, request):
    from app.crm import encode
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from uuid import uuid4
    validate_categories(request.categories)
    _validate_automation_timing(request, None)
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                """INSERT INTO soloestate.automation_policies(id,kind,hours_before,days_after,after_event,frequency,
                   categories,lead_statuses,max_attempts,per_lead_cooldown_hours,wording,enabled)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false) RETURNING *""",
                (uuid4(), request.kind, request.hours_before, request.days_after, request.after_event, request.frequency,
                 request.categories, request.lead_statuses, request.max_attempts, request.per_lead_cooldown_hours,
                 request.wording)).fetchone()
            return {'record': row}
    return crm.mutate('automation-policy:create', request, work)


def update_automation_policy(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    from psycopg import sql
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.automation_policies WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Policy changed. Reload before editing.')
            provided = request.model_fields_set
            changes = {field: getattr(request, field) for field in
                       ('hours_before', 'days_after', 'after_event', 'frequency', 'categories', 'lead_statuses',
                        'max_attempts', 'per_lead_cooldown_hours', 'wording', 'enabled')
                       if field in provided and getattr(request, field) is not None}
            if not changes:
                raise CRMError('No policy change requested.')
            if 'categories' in changes:
                validate_categories(changes['categories'])
            merged = dict(before, **changes)
            _validate_automation_timing(request, merged)
            if merged['enabled'] and (not merged['categories'] or not merged['lead_statuses']):
                raise CRMError('Enabling a policy requires categories and lead statuses.', 422)
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(field)) for field in changes)
            row = cur.execute(sql.SQL('UPDATE soloestate.automation_policies SET {} WHERE id=%s RETURNING *').format(assignments),
                              (*changes.values(), identifier)).fetchone()
            return {'record': row}
    return crm.mutate('automation-policy:update', request, work)


def _validate_automation_timing(request, merged):
    from app.crm import CRMError
    source = merged if merged is not None else request
    kind = source['kind'] if isinstance(source, dict) else source.kind
    hours = source['hours_before'] if isinstance(source, dict) else source.hours_before
    days = source['days_after'] if isinstance(source, dict) else source.days_after
    event = source['after_event'] if isinstance(source, dict) else source.after_event
    if kind == 'booking_reminder' and (hours is None or days is not None or event is not None):
        raise CRMError('Booking reminders need hours_before only.', 422)
    if kind == 'followup_nudge' and (hours is not None or days is None or not event):
        raise CRMError('Follow-up nudges need days_after and after_event only.', 422)


def list_automation_policies(crm):
    from app.crm import encode
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute('SELECT * FROM soloestate.automation_policies ORDER BY created_at, id').fetchall()
    items = [dict(row) for row in rows]
    return encode({'items': items, 'total': len(items), 'page': 1, 'page_size': len(items)})
