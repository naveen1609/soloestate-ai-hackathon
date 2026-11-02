"""Channel-independent transactional booking service. D1: owner API plus synthetic
harness with fictional direct requests; adapters invoke it in D2.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

PROPOSAL_TTL = timedelta(minutes=30)
REQUEST_TTL = timedelta(hours=72)
GRID_MINUTES = 5

POLICY_REASONS = frozenset({
    'holiday_date', 'weekly_holiday', 'outside_working_day', 'outside_hours',
    'short_notice', 'bad_duration'})


def normalize_window(start, duration_min):
    floored = start.replace(minute=(start.minute // GRID_MINUTES) * GRID_MINUTES, second=0, microsecond=0)
    return floored, floored + timedelta(minutes=duration_min)


def governing(row):
    if row['owner_approved_start'] is not None:
        return row['owner_approved_start'], row['owner_approved_end'], row['owner_approved_resource_id']
    if row['customer_confirmed_start'] is not None:
        return row['customer_confirmed_start'], row['customer_confirmed_end'], row['customer_confirmed_resource_id']
    return None


def request_hash_for(*, lead_id, property_id, start, end, resource_id, duration_min, policy_version, reasons):
    from app.owner import payload_hash
    return payload_hash({'lead_id': str(lead_id), 'property_id': str(property_id) if property_id else None,
                         'start': start.isoformat(), 'end': end.isoformat(),
                         'resource_id': str(resource_id) if resource_id else None,
                         'duration_min': duration_min, 'policy_version': policy_version,
                         'reasons': sorted(reasons)})


def snap(row):
    from app.crm import encode
    if row is None:
        return None
    return {'booking_request': encode({key: row[key] for key in
            ('id', 'lead_id', 'property_id', 'channel', 'requested_start', 'requested_end',
             'requested_resource_id', 'requested_duration_min', 'customer_confirmed_start',
             'customer_confirmed_end', 'customer_confirmed_resource_id', 'owner_approved_start',
             'owner_approved_end', 'owner_approved_resource_id', 'needs_owner_approval',
             'exception_reasons', 'status', 'approval_id', 'booking_id', 'policy_version',
             'expires_at', 'version')})}


def log_activity(cur, lead_id, before, after, reason, request_key, actor='owner'):
    from psycopg.types.json import Jsonb
    cur.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
        VALUES (%s,%s,%s,%s,%s,%s)""", (lead_id, actor, reason, Jsonb(snap(before)), Jsonb(snap(after)), request_key))


def _contact_gate(lead):
    from app.crm import CRMError
    if lead['contact_permission'] == 'opted_out' or lead['contact_preference'] == 'none':
        raise CRMError('Customer contact is opted out or disabled.')
    if lead['handoff_state'] in {'requested', 'in_progress'}:
        raise CRMError('Resolve the human handoff before booking actions.')


def _first_free_resource(cur, start, end, duration_min, policy):
    from app.policies import OWNER_RESOURCE_ID, conflicts
    ordered = cur.execute("""SELECT id FROM soloestate.resources WHERE active AND kind='person'
        ORDER BY id=%s DESC, name, id""", (OWNER_RESOURCE_ID,)).fetchall()
    for candidate in ordered:
        if not conflicts(cur, candidate['id'], start, end, policy['buffer_minutes']):
            return candidate['id']
    return None


def alternatives(cur, property_id, after, duration_min, policy, now, limit=3):
    from app.policies import availability_windows, site_capacity_ok
    found, cursor, horizon = [], after, after + timedelta(days=7)
    while cursor < horizon and len(found) < limit:
        chunk_end = min(cursor + timedelta(days=1), horizon)
        for window in availability_windows(cur, cursor, chunk_end, policy=dict(policy), now=now, limit=limit - len(found) + 1):
            start, end = window['starts_at'], window['ends_at']
            if (end - start).total_seconds() / 60 < duration_min:
                continue
            resource_id = _first_free_resource(cur, start, end, duration_min, policy)
            if resource_id is None or not site_capacity_ok(cur, property_id, start, end):
                continue
            found.append({**window, 'resource_id': resource_id})
            if len(found) >= limit:
                break
        cursor = chunk_end
    return found


def propose(crm, request):
    from app.crm import CRMError, encode, found
    from app.policies import evaluate_window
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (request.lead_id,)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before requesting a booking.', 422)
            _contact_gate(lead)
            if request.property_id is None:
                raise CRMError('Booking requests require a property.', 422)
            prop = cur.execute('SELECT * FROM soloestate.properties WHERE id=%s FOR SHARE', (request.property_id,)).fetchone()
            if prop is None or not prop['active']:
                raise CRMError('Booking requests require an active property.', 422)
            if request.conversation_id is not None:
                convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (request.conversation_id,)).fetchone()
                if convo is None or convo['lead_id'] != lead['id']:
                    raise CRMError('Conversation does not belong to this lead.', 422)
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            duration = request.requested_duration_min or policy['default_duration_min']
            start, end = normalize_window(request.requested_start, duration)
            verdict = evaluate_window(dict(policy), start, end, now, duration)
            reasons = list(verdict['reasons'])
            resource_id = request.requested_resource_id
            if resource_id is not None:
                resource = cur.execute('SELECT * FROM soloestate.resources WHERE id=%s', (resource_id,)).fetchone()
                if resource is None or not resource['active']:
                    raise CRMError('Requested resource is not available.', 422)
            from app.policies import conflicts, site_capacity_ok
            if resource_id is not None and conflicts(cur, resource_id, start, end, policy['buffer_minutes']):
                reasons.append('resource_unavailable')
            if resource_id is None:
                resource_id = _first_free_resource(cur, start, end, duration, policy)
                if resource_id is None:
                    reasons.append('resource_unavailable')
            if not site_capacity_ok(cur, prop['id'], start, end):
                reasons.append('site_capacity')
            exact = (start == request.requested_start and end == request.requested_end)
            expires_at = min(start, now + REQUEST_TTL)
            if not reasons and exact:
                status, confirmed, needs_owner = 'approved', True, False
            elif not reasons:
                status, confirmed, needs_owner = 'proposed', False, False
            else:
                status, confirmed, needs_owner = 'awaiting_owner', False, True
            row = cur.execute(
                """INSERT INTO soloestate.booking_requests(id,lead_id,property_id,conversation_id,channel,
                   requested_start,requested_end,requested_resource_id,requested_duration_min,
                   customer_confirmed_start,customer_confirmed_end,customer_confirmed_resource_id,
                   proposal_expires_at,needs_owner_approval,exception_reasons,status,policy_version,
                   request_hash,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (uuid4(), lead['id'], prop['id'], request.conversation_id, request.channel,
                 request.requested_start, request.requested_end, request.requested_resource_id, duration,
                 start if confirmed else None, end if confirmed else None, resource_id if confirmed else None,
                 now + PROPOSAL_TTL if status == 'proposed' else None,
                 needs_owner, reasons, status, policy['version'],
                 request_hash_for(lead_id=lead['id'], property_id=prop['id'], start=start, end=end,
                                  resource_id=resource_id if confirmed or status == 'awaiting_owner' else None,
                                  duration_min=duration, policy_version=policy['version'], reasons=reasons),
                 expires_at)).fetchone()
            log_activity(cur, lead['id'], None, row,
                         'Booking request proposed' if status != 'approved' else 'Booking request auto-approved by policy',
                         request.request_key)
            alts = [] if status == 'approved' else alternatives(cur, prop['id'], start, duration, policy, now)
            return {'record': row, 'normalized_start': start, 'normalized_end': end,
                    'exact_grid': exact, 'alternatives': alts}
    return crm.mutate('booking-request:create', request, work)


def _expire_if_elapsed(cur, row, now):
    if row['status'] in ('proposed', 'awaiting_owner', 'approved') and now >= row['expires_at']:
        return cur.execute("UPDATE soloestate.booking_requests SET status='expired' WHERE id=%s RETURNING *",
                           (row['id'],)).fetchone()
    return row


def confirm(crm, identifier, request):
    from app.crm import CRMError, found
    from app.policies import evaluate_window
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            row = found(cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if row['version'] != request.expected_version:
                raise CRMError('Request changed. Reload before confirming.')
            row = _expire_if_elapsed(cur, row, now)
            if row['status'] != 'proposed':
                if row['status'] == 'expired':
                    log_activity(cur, row['lead_id'], row, row, 'Customer confirmation refused: request expired', request.request_key)
                    return {'record': row, 'confirmed': False,
                            'message': 'This request has expired. Submit a new request.'}
                raise CRMError('This request no longer accepts customer confirmation.')
            if now >= row['proposal_expires_at']:
                updated = cur.execute("UPDATE soloestate.booking_requests SET status='expired' WHERE id=%s RETURNING *",
                                      (identifier,)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Proposed window lapsed before confirmation', request.request_key)
                return {'record': updated, 'confirmed': False,
                        'message': 'The proposed window lapsed. Submit a new request.'}
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            duration = row['requested_duration_min']
            start, end = normalize_window(row['requested_start'], duration)
            verdict = evaluate_window(dict(policy), start, end, now, duration)
            reasons = list(verdict['reasons'])
            resource_id = row['requested_resource_id']
            from app.policies import conflicts, site_capacity_ok
            if resource_id is not None and conflicts(cur, resource_id, start, end, policy['buffer_minutes']):
                reasons.append('resource_unavailable')
            if resource_id is None:
                resource_id = _first_free_resource(cur, start, end, duration, policy)
                if resource_id is None:
                    reasons.append('resource_unavailable')
            if not site_capacity_ok(cur, row['property_id'], start, end):
                reasons.append('site_capacity')
            if reasons:
                updated = cur.execute(
                    """UPDATE soloestate.booking_requests SET exception_reasons=%s, needs_owner_approval=true,
                       status='awaiting_owner', request_hash=%s WHERE id=%s RETURNING *""",
                    (reasons, request_hash_for(lead_id=row['lead_id'], property_id=row['property_id'], start=start,
                                               end=end, resource_id=resource_id, duration_min=duration,
                                               policy_version=policy['version'], reasons=reasons), identifier)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Confirmation found changed conditions; owner review needed', request.request_key)
                return {'record': updated, 'confirmed': False, 'reasons': reasons,
                        'message': 'Conditions changed since the proposal. The owner will review alternatives.'}
            updated = cur.execute(
                """UPDATE soloestate.booking_requests SET customer_confirmed_start=%s, customer_confirmed_end=%s,
                   customer_confirmed_resource_id=%s, status='approved', request_hash=%s,
                   expires_at=LEAST(%s, created_at + make_interval(hours=>72)) WHERE id=%s RETURNING *""",
                (start, end, resource_id, request_hash_for(lead_id=row['lead_id'], property_id=row['property_id'],
                 start=start, end=end, resource_id=resource_id, duration_min=duration,
                 policy_version=policy['version'], reasons=[]), start, identifier)).fetchone()
            log_activity(cur, row['lead_id'], row, updated, 'Customer confirmed the proposed window', request.request_key)
            return {'record': updated, 'confirmed': True}
    return crm.mutate('booking-request:confirm', request, work)


def decide(crm, identifier, request):
    from app.crm import CRMError, found
    from app.policies import evaluate_window
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            row = found(cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if row['version'] != request.expected_version:
                raise CRMError('Request changed. Reload before deciding.')
            row = _expire_if_elapsed(cur, row, now)
            if row['status'] != 'awaiting_owner':
                if row['status'] == 'expired':
                    log_activity(cur, row['lead_id'], row, row, 'Owner decision refused: request expired', request.request_key)
                    return {'record': row, 'decided': False,
                            'message': 'This request has expired. Submit a new request.'}
                raise CRMError('This request no longer accepts an owner decision.')
            if row['request_hash'] != request.expected_request_hash:
                raise CRMError('Request changed. Reload before deciding.')
            if request.decision == 'denied':
                updated = cur.execute("UPDATE soloestate.booking_requests SET status='denied' WHERE id=%s RETURNING *",
                                      (identifier,)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Owner denied the booking request: ' + request.reason.strip(), request.request_key)
                return {'record': updated, 'decided': True}
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (row['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before approving a booking.', 422)
            prop = cur.execute('SELECT * FROM soloestate.properties WHERE id=%s FOR SHARE', (row['property_id'],)).fetchone()
            if prop is None or not prop['active']:
                raise CRMError('Booking approval requires an active property.', 422)
            if request.approved_start is not None:
                start, end = request.approved_start, request.approved_end
            elif row['customer_confirmed_start'] is not None:
                start, end = row['customer_confirmed_start'], row['customer_confirmed_end']
            else:
                start, end = normalize_window(row['requested_start'], row['requested_duration_min'])
            resource_id = request.approved_resource_id or row['customer_confirmed_resource_id'] or row['requested_resource_id']
            if resource_id is not None:
                resource = cur.execute('SELECT * FROM soloestate.resources WHERE id=%s', (resource_id,)).fetchone()
                if resource is None or not resource['active']:
                    raise CRMError('Approved resource is not available.', 422)
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            if resource_id is None:
                resource_id = _first_free_resource(cur, start, end, row['requested_duration_min'], policy)
            verdict = evaluate_window(dict(policy), start, end, now, row['requested_duration_min'])
            reasons = list(verdict['reasons'])
            updated = cur.execute(
                """UPDATE soloestate.booking_requests SET owner_approved_start=%s, owner_approved_end=%s,
                   owner_approved_resource_id=%s, exception_reasons=%s, status='approved', request_hash=%s,
                   expires_at=LEAST(%s, created_at + make_interval(hours=>72)) WHERE id=%s RETURNING *""",
                (start, end, resource_id, reasons, request_hash_for(
                    lead_id=row['lead_id'], property_id=row['property_id'], start=start, end=end,
                    resource_id=resource_id, duration_min=row['requested_duration_min'],
                    policy_version=policy['version'], reasons=reasons), start, identifier)).fetchone()
            log_activity(cur, row['lead_id'], row, updated, 'Owner approved the booking request: ' + request.reason.strip(), request.request_key)
            return {'record': updated, 'decided': True}
    return crm.mutate('booking-request:decide', request, work)


def commit(crm, identifier, request, actor='owner'):
    from app.crm import CRMError, encode, found
    from app.policies import conflicts, evaluate_window, site_capacity_ok
    from app.tools.service import _booking_snapshot
    from app.tools.schemas import ToolContext
    from app.tools.service import BusinessTools
    from app.agent.store import RunStore
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            controls = cur.execute('SELECT * FROM soloestate.automation_controls WHERE id=%s FOR UPDATE', ('default',)).fetchone()
            row = found(cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if controls['emergency_stop'] and row['status'] in ('proposed', 'awaiting_owner', 'approved'):
                updated = cur.execute("""UPDATE soloestate.booking_requests SET status='awaiting_owner',
                    exception_reasons=array_append(exception_reasons,'emergency_stop') WHERE id=%s RETURNING *""",
                    (identifier,)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Autonomous commit blocked by emergency stop', request.request_key, actor)
                return {'record': updated, 'committed': False, 'reasons': list(updated['exception_reasons']),
                        'message': 'Emergency stop is active. Lift it or book through the direct owner workflow.'}
            window = governing(row)
            start, end, resource_id = window if window is not None else (None, None, None)
            duration = row['requested_duration_min']
            # Section-6 lock order: resource -> slot -> property -> lead. The
            # property row is taken FOR UPDATE directly (never upgraded from
            # SHARE) so the capacity count below is authoritative; later
            # snapshot/trigger re-locks are no-ops on these held locks.
            # Windowless rows (proposed, fresh awaiting_owner) skip the
            # resource/slot locks and exit at the status gate below.
            resource = cur.execute('SELECT * FROM soloestate.resources WHERE id=%s FOR UPDATE', (resource_id,)).fetchone() if resource_id else None
            slot = None
            if resource is not None:
                slot = cur.execute("""SELECT * FROM soloestate.visit_slots WHERE resource_id=%s AND starts_at=%s AND ends_at=%s
                    FOR UPDATE""", (resource_id, start, end)).fetchone()
            prop = cur.execute('SELECT * FROM soloestate.properties WHERE id=%s FOR UPDATE', (row['property_id'],)).fetchone()
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (row['lead_id'],)).fetchone())
            # Fresh database clock after all blocking locks, immediately before
            # the decision boundary: expiry and the moving notice window are
            # evaluated here, never on a pre-lock timestamp.
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            row = _expire_if_elapsed(cur, row, now)
            if row['status'] != 'approved':
                if row['status'] == 'expired':
                    log_activity(cur, row['lead_id'], row, row, 'Commit refused: request expired', request.request_key, actor)
                    return {'record': row, 'committed': False,
                            'message': 'This request has expired. Submit a new request.'}
                raise CRMError('Only an approved request can be committed.')
            if window is None:
                raise CRMError('Approved request has no governing window.')
            if row['request_hash'] != request.expected_request_hash:
                raise CRMError('Request changed. Reload before committing.')
            verdict = evaluate_window(dict(policy), start, end, now, duration)
            allowed = set(row['exception_reasons'])
            fresh = [reason for reason in verdict['reasons'] if reason not in allowed]
            if fresh:
                updated = cur.execute("""UPDATE soloestate.booking_requests SET exception_reasons=%s,
                    needs_owner_approval=true, status='awaiting_owner' WHERE id=%s RETURNING *""",
                    (verdict['reasons'], identifier)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Commit found changed policy conditions; owner review needed', request.request_key, actor)
                return {'record': updated, 'committed': False, 'reasons': list(verdict['reasons']),
                        'message': 'Policy conditions changed since approval. The owner must review again.'}
            problems = []
            if lead['archived_at'] is not None:
                problems.append('archived_lead')
            if prop is None or not prop['active']:
                problems.append('inactive_property')
            if resource is None or not resource['active']:
                problems.append('resource_unavailable')
            elif conflicts(cur, resource_id, start, end, policy['buffer_minutes']):
                problems.append('resource_unavailable')
            if not site_capacity_ok(cur, row['property_id'], start, end):
                problems.append('site_capacity')
            if problems:
                updated = cur.execute("""UPDATE soloestate.booking_requests SET exception_reasons=%s,
                    needs_owner_approval=true, status='awaiting_owner' WHERE id=%s RETURNING *""",
                    (problems, identifier)).fetchone()
                log_activity(cur, row['lead_id'], row, updated, 'Commit found integrity failures: ' + ', '.join(problems), request.request_key, actor)
                return {'record': updated, 'committed': False, 'reasons': problems,
                        'message': 'Booking cannot be committed: ' + ', '.join(problems) + '.'}
            if slot is None and resource is not None:
                covering = cur.execute("""SELECT 1 FROM soloestate.visit_slots WHERE resource_id=%s
                    AND tstzrange(starts_at,ends_at) && tstzrange(%s,%s) LIMIT 1""",
                    (resource_id, start, end)).fetchone()
                if covering:
                    raise CRMError('No exact slot row covers this window; regenerate the availability grid for this resource.')
                slot = cur.execute("""INSERT INTO soloestate.visit_slots(id,starts_at,ends_at,resource_id)
                    VALUES (%s,%s,%s,%s) RETURNING *""",
                    (uuid4(), start, end, resource_id)).fetchone()
            run = uuid4()
            conn.execute("""INSERT INTO soloestate.runs(id,request_key,goal,status,origin,plan)
                VALUES (%s,%s,'Autonomous booking commit','pending','autonomy',%s)""",
                (run, 'autonomy:commit:' + str(identifier), Jsonb({'booking_request_id': str(identifier)})))
            snapshot = _booking_snapshot(cur, row['lead_id'], row['property_id'], slot['id'], resource_id)
            approval = cur.execute("""INSERT INTO soloestate.approvals
                (id,run_id,kind,lead_id,property_id,slot_id,idempotency_key,payload)
                VALUES (%s,%s,'booking',%s,%s,%s,%s,%s) RETURNING *""",
                (uuid4(), run, row['lead_id'], row['property_id'], slot['id'],
                 'autocommit:' + str(identifier), Jsonb(snapshot))).fetchone()
            from app.owner import payload_hash
            owner_decided = row['owner_approved_start'] is not None
            decision_actor = 'owner' if owner_decided else 'policy'
            decided = cur.execute("""INSERT INTO soloestate.approval_decisions
                (id,approval_id,idempotency_key,decision,payload_hash,actor) VALUES (%s,%s,%s,'approved',%s,%s)
                RETURNING decided_at""",
                (uuid4(), approval['id'], ('owner-exception:' if owner_decided else 'autocommit:') + str(identifier),
                 payload_hash(snapshot), decision_actor)).fetchone()
            cur.execute('UPDATE soloestate.approvals SET status=%s,decided_at=%s WHERE id=%s',
                        ('approved', decided['decided_at'], approval['id']))
            from app.crm import CRM
            factory = lambda: CRM.nested(conn)
            result = BusinessTools(factory).execute('book_site_visit',
                {'approval_id': str(approval['id']), 'idempotency_key': 'autocommit-book:' + str(identifier)},
                ToolContext(run_id=run))
            if result.status != 'succeeded':
                raise CRMError(result.message or 'Booking could not be committed.')
            RunStore(factory).finish(run, 'completed', 'autonomy_booking_committed', [], [], connection=conn)
            updated = cur.execute("""UPDATE soloestate.booking_requests SET status='booked', approval_id=%s,
                booking_id=%s WHERE id=%s RETURNING *""",
                (approval['id'], result.data['booking']['id'], identifier)).fetchone()
            log_activity(cur, row['lead_id'], row, updated,
                         'Autonomous booking committed by ' + ('owner exception approval' if owner_decided else 'policy'),
                         request.request_key, actor)
            return {'record': updated, 'booking_id': result.data['booking']['id'], 'committed': True}
    return crm.mutate('booking-request:commit', request, work, actor)


def cancel(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            row = found(cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if row['version'] != request.expected_version:
                raise CRMError('Request changed. Reload before cancelling.')
            if row['status'] in ('booked', 'denied', 'expired', 'cancelled'):
                raise CRMError('This request is already terminal.')
            updated = cur.execute("UPDATE soloestate.booking_requests SET status='cancelled' WHERE id=%s RETURNING *",
                                  (identifier,)).fetchone()
            log_activity(cur, row['lead_id'], row, updated, 'Booking request cancelled: ' + request.reason.strip(), request.request_key)
            return {'record': updated}
    return crm.mutate('booking-request:cancel', request, work)


def listing(crm, *, lead_id=None, status=None, page=1, page_size=20):
    from app.crm import CRMError, encode
    from psycopg.rows import dict_row
    if status is not None and status not in ('proposed', 'awaiting_owner', 'approved', 'denied', 'expired', 'booked', 'cancelled'):
        raise CRMError('Invalid request status.', 422)
    conditions, values = [], []
    if lead_id is not None:
        conditions.append('lead_id=%s')
        values.append(lead_id)
    if status is not None:
        conditions.append('status=%s')
        values.append(status)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM soloestate.booking_requests{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f'SELECT * FROM soloestate.booking_requests{clause} ORDER BY created_at DESC, id LIMIT %s OFFSET %s',
                               [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))


def detail(crm, identifier):
    from app.crm import encode, found
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = found(cur.execute('SELECT * FROM soloestate.booking_requests WHERE id=%s', (identifier,)).fetchone())
            policy = cur.execute("SELECT * FROM soloestate.business_policies WHERE id='default'").fetchone()
            now = cur.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            response = {'record': row, 'alternatives': []}
            if row['status'] in ('proposed', 'awaiting_owner') and row['property_id'] is not None:
                window = governing(row)
                anchor = window[0] if window else row['requested_start']
                response['alternatives'] = alternatives(cur, row['property_id'], anchor, row['requested_duration_min'],
                                                        policy, now)
    return encode(response)
