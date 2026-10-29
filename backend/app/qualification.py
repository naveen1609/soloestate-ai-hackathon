"""Shared qualification facts and deterministic matching. No model-generated preferences."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal

Timeframe = Literal['within_3_months', 'three_to_six_months', 'later', 'no_fixed_deadline']
REQUIREMENTS = ('budget_min', 'budget_max', 'preferred_localities', 'bedrooms', 'purchase_timeframe')


def facts(lead):
    return {
        'budget_min': format(Decimal(lead['budget_min']), '.2f') if lead['budget_min'] is not None else None,
        'budget_max': format(Decimal(lead['budget_max']), '.2f') if lead['budget_max'] is not None else None,
        'preferred_localities': sorted({value.strip().lower() for value in lead['preferred_localities']}) if lead['preferred_localities'] else None,
        'bedrooms': lead['bedrooms'],
        'purchase_timeframe': lead.get('purchase_timeframe'),
    }


def fingerprint(lead):
    return sha256(json.dumps(facts(lead), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def readiness(lead):
    values = facts(lead)
    matching_missing = [field for field in REQUIREMENTS[:-1] if values[field] is None]
    invalid = []
    if lead['budget_max'] is not None and Decimal(lead['budget_max']) <= 0:
        invalid.append('budget_max')
    missing = matching_missing + (['purchase_timeframe'] if values['purchase_timeframe'] is None else [])
    ready = not missing and not invalid
    current = ready and lead['journey_stage'] == 'qualified' and lead.get('qualification_fingerprint') == fingerprint(lead)
    return dict(matching_ready=not matching_missing and not invalid, qualification_ready=ready,
                matching_missing_fields=matching_missing, missing_fields=missing, invalid_fields=invalid,
                qualification_current=current, needs_review=lead['journey_stage'] == 'qualified' and not current)


def reviewed_fingerprint(lead):
    from app.crm import CRMError
    result = readiness(lead)
    if not result['qualification_ready']:
        raise CRMError('Qualification is incomplete. Review: ' + ', '.join(result['missing_fields'] + result['invalid_fields']))
    return fingerprint(lead)


def activity(cur, before, after, reason, request_key):
    from psycopg.types.json import Jsonb
    from app.journey import FIELDS
    def snapshot(lead):
        return {**{field: lead[field] for field in FIELDS}, 'qualification': facts(lead),
                'qualification_fingerprint': lead.get('qualification_fingerprint')}
    cur.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
        VALUES (%s,'owner',%s,%s,%s,%s)""",
        (after['id'], reason, Jsonb(snapshot(before)), Jsonb(snapshot(after)), request_key))


def confirm(crm, identifier, request):
    from psycopg.rows import dict_row
    from app.crm import found, CRMError
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before qualifying.')
            if before['archived_at'] is not None:
                raise CRMError('Restore the lead before qualifying.')
            if before['journey_stage'] not in {'qualifying', 'qualified'}:
                raise CRMError('Move the lead to qualifying before confirming qualification.')
            digest = reviewed_fingerprint(before)
            after = cur.execute("UPDATE soloestate.leads SET journey_stage='qualified',qualification_fingerprint=%s WHERE id=%s RETURNING *", (digest, identifier)).fetchone()
            activity(cur, before, after, request.reason, request.request_key)
            return {'record': after}
    return crm.mutate('lead_qualify:' + str(identifier), request, work)


def property_matches(cur, lead, limit):
    """Called by both owner API and registered read tool; same filters and explanations."""
    from app.tools.schemas import MatchesOutput
    state = readiness(lead)
    common = dict(lead_id=lead['id'], lead_version=lead['version'], checked_at=datetime.now(timezone.utc),
                  matching_ready=state['matching_ready'], missing_fields=state['matching_missing_fields'],
                  invalid_fields=state['invalid_fields'], criteria_snapshot=facts(lead))
    if not state['matching_ready']:
        return MatchesOutput(matches=[], truncated=False, **common)
    rows = cur.execute("""SELECT id,title,locality,price,bedrooms,description,version,
        CASE WHEN bedrooms=%s THEN 100 ELSE 90 END AS score
        FROM soloestate.properties WHERE active AND price BETWEEN %s AND %s
        AND bedrooms >= %s AND lower(btrim(locality)) = ANY(%s::text[])
        ORDER BY CASE WHEN bedrooms=%s THEN 0 ELSE 1 END, price, id LIMIT %s""",
        (lead['bedrooms'], lead['budget_min'], lead['budget_max'], lead['bedrooms'],
         facts(lead)['preferred_localities'], lead['bedrooms'], limit + 1)).fetchall()
    for row in rows:
        row['reasons'] = ['within_budget', 'preferred_locality', 'exact_bedrooms' if row['bedrooms'] == lead['bedrooms'] else 'additional_bedrooms']
        row['checks'] = [
            dict(criterion='active', requested=True, actual=True, passed=True),
            dict(criterion='budget', requested={'min': lead['budget_min'], 'max': lead['budget_max']}, actual=row['price'], passed=True),
            dict(criterion='locality', requested=facts(lead)['preferred_localities'], actual=row['locality'], passed=True),
            dict(criterion='minimum_bedrooms', requested=lead['bedrooms'], actual=row['bedrooms'], passed=True),
        ]
    return MatchesOutput(matches=rows[:limit], truncated=len(rows) > limit, **common)
