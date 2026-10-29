"""Owner-recorded visit outcomes and visit follow-up. Internal records only; nothing is delivered or inferred."""
from psycopg.rows import dict_row

OUTCOMES = ('completed', 'no_show')
FOLLOWUP = {'none': {'pending'}, 'pending': {'done', 'none'}, 'done': {'pending'}}


def visit_result(status, outcome, has_replacement):
    if outcome == 'completed':
        return 'completed'
    if outcome == 'no_show':
        return 'no_show'
    if status == 'cancelled':
        return 'rescheduled' if has_replacement else 'cancelled'
    return 'scheduled'


def snap(row):
    from app.crm import encode
    if row is None:
        return None
    return {'visit': encode({'id': row['id'], 'lead_id': row['lead_id'], 'property_id': row['property_id'],
        'slot_id': row['slot_id'], 'status': row['status'], 'replaces_booking_id': row['replaces_booking_id'],
        'visit_outcome': row['visit_outcome'], 'outcome_note': row['outcome_note'],
        'followup_state': row['followup_state'], 'followup_note': row['followup_note'], 'version': row['version']})}


def log_activity(cur, before, after, reason, request_key):
    from psycopg.types.json import Jsonb
    cur.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
        VALUES (%s,'owner',%s,%s,%s,%s)""", (after['lead_id'], reason, Jsonb(snap(before)), Jsonb(snap(after)), request_key))


def record_outcome(crm, identifier, request):
    from app.crm import CRM, CRMError, found
    from psycopg import sql
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            booking = found(cur.execute('SELECT * FROM soloestate.bookings WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if booking['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before editing.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (booking['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before recording a visit outcome.', 422)
            provided = request.model_fields_set
            changes = {}
            outcome_value = request.visit_outcome if 'visit_outcome' in provided else None
            outcome_note_given = 'outcome_note' in provided and request.outcome_note is not None
            if booking['status'] != 'confirmed' and (outcome_value is not None or outcome_note_given):
                raise CRMError('Outcomes are recorded only on confirmed visits.')
            if booking['visit_outcome'] is not None:
                if outcome_value is not None or outcome_note_given:
                    raise CRMError('Recorded visit outcomes are terminal; history is preserved.')
            elif outcome_value is not None or outcome_note_given:
                if outcome_value is None:
                    raise CRMError('Recording an outcome is required with an outcome note.', 422)
                changes['visit_outcome'] = outcome_value
                if outcome_note_given:
                    note = request.outcome_note.strip()
                    if not note:
                        raise CRMError('Outcome note must be nonempty.', 422)
                    changes['outcome_note'] = note
            state, note = booking['followup_state'], booking['followup_note']
            target = request.followup_state if 'followup_state' in provided and request.followup_state is not None else state
            note_given = 'followup_note' in provided and request.followup_note is not None
            if target != state:
                if target not in FOLLOWUP[state]:
                    raise CRMError('Invalid follow-up transition.')
                changes['followup_state'] = target
                if target == 'none':
                    if note_given:
                        raise CRMError('Follow-up note is cleared when withdrawing.', 422)
                    changes['followup_note'] = None
                elif target == 'pending' and state == 'none' and not note_given:
                    raise CRMError('A next-action note is required while follow-up is pending.', 422)
                elif note_given:
                    final = request.followup_note.strip()
                    if not final:
                        raise CRMError('Follow-up note must be nonempty.', 422)
                    if final != note:
                        changes['followup_note'] = final
            elif note_given:
                if state == 'none':
                    raise CRMError('Start visit follow-up before adding a note.', 422)
                final = request.followup_note.strip()
                if not final:
                    raise CRMError('Follow-up note must be nonempty.', 422)
                if final != note:
                    changes['followup_note'] = final
            if not changes:
                raise CRMError('No visit change requested.')
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(field)) for field in changes)
            after = cur.execute(sql.SQL('UPDATE soloestate.bookings SET {} WHERE id=%s RETURNING *').format(assignments),
                                (*changes.values(), identifier)).fetchone()
            log_activity(cur, booking, after, request.reason.strip(), request.request_key)
            return {'record': CRM._appointment_row(conn, identifier)}
    return crm.mutate(f'appointment:outcome:{identifier}', request, work)
