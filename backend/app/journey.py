"""Owner-managed lead state; never a model tool or delivery permission grant."""
from typing import Literal
from pydantic import Field, model_validator
from psycopg.rows import dict_row
from app.crm_schemas import Versioned

STAGES = {
    'new': {'qualifying', 'disqualified', 'closed'},
    'qualifying': {'qualified', 'disqualified', 'closed'},
    'qualified': {'qualifying', 'disqualified', 'closed'},
    'disqualified': {'qualifying'}, 'closed': {'qualifying'},
}
HANDOFF = {'none': {'requested'}, 'requested': {'in_progress', 'resolved'},
           'in_progress': {'resolved'}, 'resolved': {'requested'}}
FIELDS = ('journey_stage', 'contact_preference', 'contact_permission', 'handoff_state')

class JourneyUpdate(Versioned):
    reason: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    journey_stage: Literal['new','qualifying','qualified','disqualified','closed'] | None = None
    contact_preference: Literal['unspecified','email','phone','none'] | None = None
    contact_permission: Literal['unknown','opted_in','opted_out'] | None = None
    handoff_state: Literal['none','requested','in_progress','resolved'] | None = None

    @model_validator(mode='after')
    def changes(self):
        if not any(getattr(self, field) is not None for field in FIELDS):
            raise ValueError('At least one state change is required')
        return self


def validate_transition(before, changes):
    from app.crm import CRMError
    for field, graph in [('journey_stage', STAGES), ('handoff_state', HANDOFF)]:
        target = changes.get(field, before[field])
        if target != before[field] and target not in graph[before[field]]:
            raise CRMError('Invalid ' + field.replace('_', ' ') + ' transition.')
    # Permission is evidence recorded by an owner, not inferred from a stage or preference.
    if before['contact_permission'] == 'opted_out' and changes.get('contact_permission') == 'unknown':
        raise CRMError('Opt-out requires explicit opted-in evidence to lift.')


def update(crm, identifier, request):
    from app.crm import found, CRMError
    from psycopg import sql
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before editing.')
            if before['archived_at'] is not None:
                raise CRMError('Restore the lead before changing journey or contact state.')
            changes = {field: getattr(request, field) for field in FIELDS if getattr(request, field) is not None and getattr(request, field) != before[field]}
            if not changes: raise CRMError('No state change requested.')
            validate_transition(before, changes)
            if changes.get('journey_stage') == 'qualified':
                from app.qualification import reviewed_fingerprint
                changes['qualification_fingerprint'] = reviewed_fingerprint(before)
            assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(field)) for field in changes)
            after = cur.execute(sql.SQL('UPDATE soloestate.leads SET {} WHERE id=%s RETURNING *').format(assignments), (*changes.values(), identifier)).fetchone()
            from app.qualification import activity
            activity(cur,before,after,request.reason.strip(),request.request_key)
            return {'record': after}
    return crm.mutate('lead_journey:'+str(identifier), request, work)


def activities(crm, identifier, page=1, page_size=20):
    from app.crm import found, encode
    with crm.connect() as conn:
        found(conn.execute('SELECT id FROM soloestate.leads WHERE id=%s', (identifier,)).fetchone())
        with conn.cursor(row_factory=dict_row) as cur:
            total = cur.execute('SELECT count(*) AS n FROM soloestate.lead_activities WHERE lead_id=%s', (identifier,)).fetchone()['n']
            rows = cur.execute('SELECT * FROM soloestate.lead_activities WHERE lead_id=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s', (identifier,page_size,(page-1)*page_size)).fetchall()
    return encode({'items': rows, 'total': total, 'page': page, 'page_size': page_size})
