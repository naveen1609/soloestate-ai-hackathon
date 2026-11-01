"""Website visitor chat on the shared conversation/lead services. Token-scoped, actor 'visitor'."""
import secrets
from uuid import uuid4

from psycopg.errors import UniqueViolation

from app.crm import CRMError, encode, found

RATE_LIMIT_PER_HOUR = 30
ALIAS_ATTEMPTS = 3


def alias_email(asserted):
    local, domain = asserted.rsplit('@', 1)
    suffix = secrets.token_hex(4)
    room = 254 - len(domain) - len(suffix) - len('@+chat-')
    return f'{local[:max(room, 1)]}+chat-{suffix}@{domain}'


def scope(token_cid, path_cid):
    if str(token_cid) != str(path_cid):
        raise CRMError('Chat token does not authorize this conversation.', 403)


def public_message(row, superseded=False):
    return encode({'id': row['id'], 'role': row['role'], 'body': row['body'], 'grounding': row['grounding'],
                   'unsupported_reason': row['unsupported_reason'], 'citations': row['citations'] or [],
                   'citations_superseded': superseded, 'confidence': row['confidence'],
                   'supersedes_message_id': row['supersedes_message_id'], 'created_at': row['created_at']})


def public_lead(row):
    from app.qualification import readiness
    return encode({'id': row['id'], 'version': row['version'], 'name': row['name'],
                   'preferred_localities': row['preferred_localities'], 'budget_min': row['budget_min'],
                   'budget_max': row['budget_max'], 'bedrooms': row['bedrooms'],
                   'purchase_timeframe': row['purchase_timeframe'], 'readiness': readiness(row)})


def public_conversation(row):
    return encode({'id': row['id'], 'lead_id': row['lead_id'], 'status': row['status'],
                   'handoff_state': row['handoff_state'], 'property_id': row['property_id'],
                   'version': row['version'], 'created_at': row['created_at']})


def start(crm, request, settings=None):
    from app.conversation import log_activity
    from app.visitor_tokens import mint
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            asserted = request.email
            exists = cur.execute('SELECT id FROM soloestate.leads WHERE lower(email)=%s', (asserted,)).fetchone()
            lead = None
            for attempt in range(1 + ALIAS_ATTEMPTS):
                candidate = asserted if attempt == 0 and exists is None else alias_email(asserted)
                notes = '' if candidate == asserted else f'Visitor asserted contact: {asserted}'
                try:
                    with conn.transaction():
                        lead = cur.execute('INSERT INTO soloestate.leads(id,name,email,notes) VALUES (%s,%s,%s,%s) RETURNING *',
                                           (uuid4(), request.name.strip(), candidate, notes)).fetchone()
                    break
                except UniqueViolation:
                    continue
            if lead is None:
                raise CRMError('Chat could not start. Try again later.', 503)
            # Same row shape as open_conversation's INSERT; kept inline so lead+conversation commit atomically.
            convo = cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING *',
                                (uuid4(), lead['id'], None)).fetchone()
            log_activity(cur, None, convo, 'Visitor started chat', request.request_key, 'visitor')
            return {'record': convo, 'lead': lead, 'token': mint(convo['id'], settings)}
    response = crm.mutate('chat:start', request, work, 'visitor')
    return {'lead': public_lead(response['lead']), 'conversation': public_conversation(response['record']),
            'messages': [], 'token': response['token']}


async def send(crm, identifier, request, model=None):
    from app.conversation import append, draft
    from app.crm_schemas import DraftRequest, MessageAppend
    appended = append(crm, identifier, MessageAppend(request_key=request.request_key, expected_version=request.expected_version,
                      role='customer', body=request.body, supersedes_message_id=None),
                      actor='visitor', rate_limit_per_hour=RATE_LIMIT_PER_HOUR, visitor_send=True)
    message = appended['record']
    internal = DraftRequest(request_key=f"chat:draft:{message['id']}", expected_version=request.expected_version,
                            message_id=message['id'])
    try:
        if model is None:
            from app.config import Settings
            from app.services.meta_model import MetaModelService
            async with MetaModelService(Settings()) as live:
                drafted = await draft(crm, identifier, internal, live, actor='visitor')
        else:
            drafted = await draft(crm, identifier, internal, model, actor='visitor')
    except CRMError as exc:
        # The customer message above is committed: never report a definitive failure that hides it.
        raise CRMError(f'{exc} Your message was saved; reload the chat to see its current state.', exc.status) from None
    return {'message': public_message(message), 'reply': public_message(drafted['record']),
            'conversation': public_conversation(drafted['conversation'])}


def resume(crm, identifier):
    from app.conversation import detail
    from psycopg.rows import dict_row
    thread = detail(crm, identifier)
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (thread['record']['lead_id'],)).fetchone())
    return {'lead': public_lead(lead), 'conversation': public_conversation(thread['record']),
            'messages': [public_message(message, message['citations_superseded']) for message in thread['messages']],
            'truncated': thread['truncated'], 'drafting_blocked': thread['drafting_blocked']}


def requirements(crm, identifier, request):
    from app.crm_schemas import LeadUpdate
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            convo = found(cur.execute('SELECT lead_id FROM soloestate.conversations WHERE id=%s', (identifier,)).fetchone())
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (convo['lead_id'],)).fetchone())
    if lead['archived_at'] is not None:
        raise CRMError('This chat is no longer available.', 422)
    fields = {name: getattr(request, name) for name in
              ('preferred_localities', 'budget_min', 'budget_max', 'bedrooms', 'purchase_timeframe')
              if name in request.model_fields_set}
    internal = LeadUpdate(request_key=request.request_key, expected_version=request.expected_version,
                          name=lead['name'], email=lead['email'], status=lead['status'], notes=lead['notes'], **fields)
    saved = crm.save('leads', internal, lead['id'], actor='visitor', freeze_conversation_id=identifier)
    return {'lead': public_lead(saved['record'])}


def matches(crm, identifier, limit=5):
    from app.qualification import property_matches
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            convo = found(cur.execute('SELECT lead_id FROM soloestate.conversations WHERE id=%s', (identifier,)).fetchone())
            lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s AND archived_at IS NULL FOR SHARE',
                               (convo['lead_id'],)).fetchone()
            if lead is None:
                raise CRMError('Active lead not found.', 404)
            return property_matches(cur, lead, limit).model_dump(mode='json')


def slots(crm, since, until):
    from datetime import timedelta
    if not timedelta(0) < until - since <= timedelta(days=31):
        raise CRMError('Availability window must be 1–31 days.', 422)
    return crm.slots(since, until)


def set_context(crm, identifier, request):
    from app.conversation import log_activity
    from app.qualification import property_matches
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            convo = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if convo['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before changing property focus.')
            if convo['status'] == 'resolved':
                raise CRMError('This chat is resolved. New visitor changes are disabled.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (convo['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('This chat is no longer available.', 422)
            prop = cur.execute('SELECT * FROM soloestate.properties WHERE id=%s FOR SHARE', (request.property_id,)).fetchone()
            if prop is None or not prop['active']:
                raise CRMError('Property focus requires an active property.', 422)
            matches = property_matches(cur, lead, 20)
            if str(request.property_id) not in {str(match.id) for match in matches.matches}:
                raise CRMError('Property focus must be one of the current matches for this chat.', 422)
            after = cur.execute('UPDATE soloestate.conversations SET property_id=%s WHERE id=%s RETURNING *',
                                (request.property_id, identifier)).fetchone()
            log_activity(cur, convo, after, 'Visitor selected property focus', request.request_key, 'visitor')
            return {'record': after}
    updated = crm.mutate(f'conversation:focus:{identifier}', request, work, 'visitor')
    return {'conversation': public_conversation(updated['record'])}


def request_handoff(crm, identifier, request):
    from app.conversation import handoff
    from app.crm_schemas import HandoffUpdate
    internal = HandoffUpdate(request_key=request.request_key, expected_version=request.expected_version,
                             action='request', reason=request.reason)
    updated = handoff(crm, identifier, internal, actor='visitor', visitor_freeze=True)
    return {'conversation': public_conversation(updated['record'])}
