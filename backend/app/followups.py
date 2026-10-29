"""Owner-managed follow-up tasks. Internal work only; nothing is delivered or scheduled."""
from uuid import uuid4

HISTORY_LIMIT = 20
TRANSITIONS = {'open': {'in_progress', 'cancelled'}, 'in_progress': {'open', 'in_review', 'cancelled'},
               'in_review': {'in_progress', 'done', 'cancelled'}, 'done': set(), 'cancelled': {'open'}}
ACTIVE_REVIEW = ('open', 'in_progress', 'in_review')


def overdue(due_at, status, now):
    return due_at is not None and due_at < now and status in ACTIVE_REVIEW


def resolve_status(current, content_changed, target):
    from app.crm import CRMError
    if content_changed and current == 'in_review' and target in ('in_review', 'done'):
        raise CRMError('Revise and resubmit for review in separate operations.')
    stage = 'in_progress' if content_changed and current == 'in_review' else current
    if target is None or target == stage:
        return stage
    if target not in TRANSITIONS[stage]:
        raise CRMError('Invalid task transition.')
    return target


def snap(row):
    from app.crm import encode
    if row is None:
        return None
    return {'task': encode({'id': row['id'], 'lead_id': row['lead_id'], 'property_id': row['property_id'],
        'title': row['title'], 'notes': row['notes'], 'due_at': row['due_at'],
        'assignee': row['assignee'], 'status': row['status'], 'outcome': row['outcome'], 'version': row['version']})}


def log_activity(cur, before, after, reason, request_key):
    from psycopg.types.json import Jsonb
    cur.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
        VALUES (%s,'owner',%s,%s,%s,%s)""", (after['lead_id'], reason, Jsonb(snap(before)), Jsonb(snap(after)), request_key))


def check_property(cur, property_id):
    from app.crm import CRMError, found
    if property_id is None:
        return
    prop = found(cur.execute('SELECT * FROM soloestate.properties WHERE id=%s FOR SHARE', (property_id,)).fetchone())
    if not prop['active']:
        raise CRMError('Tasks require an active property context.', 422)


def create_task(crm, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (request.lead_id,)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before creating a task.', 422)
            check_property(cur, request.property_id)
            row = cur.execute("""INSERT INTO soloestate.followup_tasks(id,lead_id,property_id,title,notes,due_at,assignee)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (uuid4(), request.lead_id, request.property_id, request.title.strip(), request.notes,
                 request.due_at, request.assignee.strip())).fetchone()
            log_activity(cur, None, row, 'Owner opened task', request.request_key)
            return {'record': row}
    return crm.mutate('followup-task:create', request, work)


def listing(crm, *, lead_id=None, status=None, page=1, page_size=20):
    from app.crm import CRMError, encode
    from psycopg.rows import dict_row
    if status is not None and status not in TRANSITIONS:
        raise CRMError('Invalid task status.', 422)
    conditions, values = [], []
    if lead_id is not None:
        conditions.append('t.lead_id=%s')
        values.append(lead_id)
    if status is not None:
        conditions.append('t.status=%s')
        values.append(status)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM soloestate.followup_tasks t{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f"""SELECT t.*,(t.due_at IS NOT NULL AND t.due_at < clock_timestamp()
                AND t.status IN ('open','in_progress','in_review')) AS overdue
                FROM soloestate.followup_tasks t{clause}
                ORDER BY t.due_at ASC NULLS LAST,t.created_at DESC,t.id LIMIT %s OFFSET %s""",
                [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))


def detail(crm, identifier):
    from app.crm import encode, found
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            record = found(cur.execute("""SELECT *,(due_at IS NOT NULL AND due_at < clock_timestamp()
                AND status IN ('open','in_progress','in_review')) AS overdue
                FROM soloestate.followup_tasks WHERE id=%s""", (identifier,)).fetchone())
            history = cur.execute("""SELECT * FROM soloestate.lead_activities WHERE lead_id=%s
                AND after_state->'task'->>'id'=%s ORDER BY created_at DESC,id DESC LIMIT %s""",
                (record['lead_id'], str(identifier), HISTORY_LIMIT)).fetchall()
    return encode({'record': record, 'history': history})


def update_task(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg import sql
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            task = found(cur.execute('SELECT * FROM soloestate.followup_tasks WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if task['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before editing.')
            if task['status'] == 'done':
                raise CRMError('Completed tasks are terminal; outcomes preserve their history.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (task['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before changing a task.', 422)
            provided = request.model_fields_set
            changes = {}
            if request.title is not None and request.title.strip() != task['title']:
                changes['title'] = request.title.strip()
            if request.notes is not None and request.notes != task['notes']:
                changes['notes'] = request.notes
            if 'due_at' in provided and request.due_at != task['due_at']:
                changes['due_at'] = request.due_at
            if request.assignee is not None and request.assignee.strip() != task['assignee']:
                changes['assignee'] = request.assignee.strip()
            if 'property_id' in provided and request.property_id != task['property_id']:
                check_property(cur, request.property_id)
                changes['property_id'] = request.property_id
            if task['status'] == 'cancelled' and changes and request.status != 'open':
                raise CRMError('Reopen the task before editing.')
            new_status = resolve_status(task['status'], bool(changes), request.status)
            if new_status != task['status']:
                changes['status'] = new_status
            if new_status == 'done':
                if request.outcome is None or not request.outcome.strip():
                    raise CRMError('Recording an outcome is required to complete a task.', 422)
                changes['outcome'] = request.outcome.strip()
            elif request.outcome is not None:
                raise CRMError('Outcome is recorded only when completing a task.', 422)
            if not changes:
                raise CRMError('No task change requested.')
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(field)) for field in changes)
            after = cur.execute(sql.SQL('UPDATE soloestate.followup_tasks SET {} WHERE id=%s RETURNING *').format(assignments),
                                (*changes.values(), identifier)).fetchone()
            log_activity(cur, task, after, request.reason.strip(), request.request_key)
            return {'record': after}
    return crm.mutate(f'followup-task:update:{identifier}', request, work)
