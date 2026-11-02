"""Owner-managed appointment resources. D1: people only; pools deferred."""
from uuid import uuid4


def listing(crm):
    from app.crm import encode
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute('SELECT * FROM soloestate.resources ORDER BY name, id').fetchall()
    items = [dict(row) for row in rows]
    return encode({'items': items, 'total': len(items), 'page': 1, 'page_size': len(items)})


def lock_ordered(cur, resource_ids):
    """Lock multiple resource rows in ascending-UUID total order (deadlock-free)."""
    from app.crm import found
    rows = []
    for rid in sorted(set(resource_ids)):
        rows.append(found(cur.execute('SELECT * FROM soloestate.resources WHERE id=%s FOR UPDATE', (rid,)).fetchone()))
    return rows


def create(crm, request):
    from app.crm import encode
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('INSERT INTO soloestate.resources(id,name) VALUES (%s,%s) RETURNING *',
                              (uuid4(), request.name.strip())).fetchone()
            return {'record': row}
    return crm.mutate('resource:create', request, work)


def deactivate(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            resource = found(cur.execute('SELECT * FROM soloestate.resources WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if resource['version'] != request.expected_version:
                raise CRMError('Resource changed. Reload before editing.')
            if not resource['active']:
                raise CRMError('Resource is already inactive.')
            future = cur.execute(
                """SELECT b.id FROM soloestate.bookings b JOIN soloestate.visit_slots s ON s.id=b.slot_id
                   WHERE b.resource_id=%s AND b.status='confirmed' AND s.starts_at>clock_timestamp()
                   ORDER BY s.starts_at LIMIT 5""", (identifier,)).fetchall()
            if future:
                raise CRMError('Reassign or cancel future bookings first: ' + ', '.join(str(row['id']) for row in future) + '.')
            row = cur.execute('UPDATE soloestate.resources SET active=false WHERE id=%s RETURNING *', (identifier,)).fetchone()
            return {'record': row, 'reason': request.reason.strip()}
    return crm.mutate('resource:deactivate', request, work)
