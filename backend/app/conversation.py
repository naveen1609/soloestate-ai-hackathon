"""Lead-linked simulated conversations with validated grounded answers. No delivery."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

MAX_CITATIONS = 5
MESSAGES_LIMIT = 100
FIXED_UNSUPPORTED_BODY = 'No verified answer is available. Owner review is required.'
GROUNDED_PREAMBLE = 'Verified answer from the knowledge base:'
MAX_ASSEMBLED_BODY = 5000  # mirrors the conversation_messages body CHECK
HANDOFF = {'none': {'requested'}, 'requested': {'resolved'}, 'resolved': {'requested'}}
REVIEW = {('pending_owner', 'acknowledge'): 'open', ('open', 'resolve'): 'resolved',
          ('pending_owner', 'resolve'): 'resolved', ('resolved', 'reopen'): 'open'}


class Citation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    faq_id: UUID
    version: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2000)


class DraftCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(max_length=MAX_CITATIONS)
    confidence: Literal['high', 'medium', 'low']


class InvalidDraft(Exception):
    pass


def normalize(text):
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def assemble_grounded(citations):
    parts = [f'[FAQ {citation.faq_id} v{citation.version}] {citation.quote.strip()}' for citation in citations]
    body = GROUNDED_PREAMBLE + '\n\n' + '\n\n'.join(parts)
    if len(body) > MAX_ASSEMBLED_BODY:
        raise ValueError('Assembled answer exceeds storage bound')
    return body


def draft_prompt(retrieved, message):
    records = [f"[{row['faq_id']} v{row['version']}] Q: {row['question'][:500]}\nA: {row['answer'][:1200]}" for row in retrieved]
    return ('Answer the customer message using ONLY the FAQ records below. Return one JSON object, no Markdown: '
            '{"answer": string up to 2000 chars, "citations": [{"faq_id": string, "version": integer, "quote": string}], '
            '"confidence": "high"|"medium"|"low"}. Cite every factual claim with the exact quoted source text. '
            'If the FAQs do not support an answer, return citations as an empty list.\n\n'
            + '\n\n'.join(records) + '\n\nCustomer message:\n' + message[:2000])


def parse_candidate(text):
    try:
        candidate = DraftCandidate.model_validate_json(text)
    except ValueError:
        raise InvalidDraft()
    if not candidate.answer.strip():
        raise InvalidDraft()
    return candidate


def validate_citations(cur, retrieved, candidate):
    if not candidate.citations:
        return 'missing_citations'
    allowed = {(str(row['faq_id']), row['version']) for row in retrieved}
    locked = {str(row['id']): row for row in cur.execute(
        'SELECT id,current_version,status FROM soloestate.faqs WHERE id=ANY(%s::uuid[]) FOR SHARE',
        ([str(citation.faq_id) for citation in candidate.citations],)).fetchall()}
    for citation in candidate.citations:
        key = str(citation.faq_id)
        stored = cur.execute('SELECT answer FROM soloestate.faq_versions WHERE faq_id=%s AND version=%s',
                             (key, citation.version)).fetchone()
        if stored is None:
            return 'invalid_citation'
        faq = locked.get(key)
        if faq is None or (key, citation.version) not in allowed or citation.version != faq['current_version'] \
                or faq['status'] != 'active':
            return 'unauthorized_faq'
        if not normalize(citation.quote) or normalize(citation.quote) not in normalize(stored['answer']):
            return 'quote_mismatch'
    return None


def blocked_reason(record):
    if record['handoff_state'] == 'requested':
        return 'handoff_requested'
    if record['status'] == 'pending_owner':
        return 'pending_owner'
    if record['status'] == 'resolved':
        return 'resolved'
    return None


def gate(record):
    from app.crm import CRMError
    reason = blocked_reason(record)
    if reason == 'handoff_requested':
        raise CRMError('Resolve the human handoff before drafting a response.')
    if reason == 'pending_owner':
        raise CRMError('Owner review is pending. Acknowledge or resolve before drafting.')
    if reason == 'resolved':
        raise CRMError('Reopen the conversation before drafting.')


def log_activity(cur, before, after, reason, request_key):
    from psycopg.types.json import Jsonb
    snap = lambda row: None if row is None else {'conversation': {'id': str(row['id']), 'status': row['status'],
                                                                 'handoff_state': row['handoff_state']}}
    cur.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
        VALUES (%s,'owner',%s,%s,%s,%s)""", (after['lead_id'], reason, Jsonb(snap(before)), Jsonb(snap(after)), request_key))


def open_conversation(crm, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (request.lead_id,)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before opening a conversation.', 422)
            if request.property_id is not None:
                prop = found(cur.execute('SELECT * FROM soloestate.properties WHERE id=%s', (request.property_id,)).fetchone())
                if not prop['active']:
                    raise CRMError('Conversations require an active property context.', 422)
            row = cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING *',
                              (uuid4(), request.lead_id, request.property_id)).fetchone()
            log_activity(cur, None, row, 'Owner opened conversation', request.request_key)
            return {'record': row}
    return crm.mutate('conversation:open', request, work)


def listing(crm, *, lead_id=None, status=None, page=1, page_size=20):
    from app.crm import CRMError, encode
    from psycopg.rows import dict_row
    if status is not None and status not in ('open', 'pending_owner', 'resolved'):
        raise CRMError('Invalid conversation status.', 422)
    conditions, values = [], []
    if lead_id is not None:
        conditions.append('c.lead_id=%s')
        values.append(lead_id)
    if status is not None:
        conditions.append('c.status=%s')
        values.append(status)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM soloestate.conversations c{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f'SELECT c.* FROM soloestate.conversations c{clause} ORDER BY c.created_at DESC,c.id LIMIT %s OFFSET %s',
                               [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))


def detail(crm, identifier):
    from app.crm import encode, found
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            record = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (identifier,)).fetchone())
            rows = cur.execute('SELECT * FROM soloestate.conversation_messages WHERE conversation_id=%s ORDER BY created_at DESC,id DESC LIMIT %s',
                               (identifier, MESSAGES_LIMIT + 1)).fetchall()
            truncated = len(rows) > MESSAGES_LIMIT
            messages = list(reversed(rows[:MESSAGES_LIMIT]))
            cited = {str(item['faq_id']) for message in messages for item in (message['citations'] or [])
                     if isinstance(item, dict) and 'faq_id' in item}
            current = {}
            if cited:
                for row in cur.execute('SELECT id,current_version,status FROM soloestate.faqs WHERE id=ANY(%s::uuid[])', (list(cited),)).fetchall():
                    current[str(row['id'])] = row
            for message in messages:
                superseded = False
                for item in message['citations'] or []:
                    faq = current.get(str(item.get('faq_id')))
                    if faq is None or faq['status'] != 'active' or item.get('version') != faq['current_version']:
                        superseded = True
                        break
                message['citations_superseded'] = superseded
    return encode({'record': record, 'messages': messages, 'truncated': truncated, 'drafting_blocked': blocked_reason(record)})


def append(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            convo = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if convo['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before messaging.')
            if convo['status'] == 'resolved':
                raise CRMError('Reopen the conversation before messaging.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (convo['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before messaging.', 422)
            if request.supersedes_message_id is not None:
                if request.role != 'owner':
                    raise CRMError('Only owner corrections link a previous message.', 422)
                target = cur.execute('SELECT id FROM soloestate.conversation_messages WHERE id=%s AND conversation_id=%s',
                                     (request.supersedes_message_id, identifier)).fetchone()
                if target is None:
                    raise CRMError('Superseded message not found in this conversation.', 404)
            row = cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,role,body,supersedes_message_id,request_key)
                VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                (identifier, request.role, request.body.strip(), request.supersedes_message_id, request.request_key)).fetchone()
            return {'record': row}
    return crm.mutate(f'conversation:message:{identifier}', request, work)


async def draft(crm, identifier, request, model):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from app.faq import retrieve
    from app.owner import payload_hash
    identity = {'action': f'conversation:draft:{identifier}', 'arguments': request.model_dump(mode='json')}
    with crm.connect() as conn:
        prior = conn.execute('SELECT fingerprint,response FROM soloestate.crm_operations WHERE request_key=%s', (request.request_key,)).fetchone()
        if prior:
            if prior[0] != payload_hash(identity):
                raise CRMError('Request key already belongs to a different operation.')
            return prior[1]
        with conn.cursor(row_factory=dict_row) as cur:
            convo = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (identifier,)).fetchone())
            if convo['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before drafting.')
            gate(convo)
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (convo['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before drafting.', 422)
            target = found(cur.execute('SELECT * FROM soloestate.conversation_messages WHERE id=%s AND conversation_id=%s',
                                       (request.message_id, identifier)).fetchone())
            if target['role'] != 'customer':
                raise CRMError('Drafts answer simulated customer messages.', 422)
            retrieved = retrieve(cur, convo, target['body'])
    from app.services.meta_model import ModelServiceError
    try:
        result = await model.generate(draft_prompt(retrieved, target['body']))
    except ModelServiceError as exc:
        result, candidate, failure = None, None, 'inference_timeout' if exc.code == 'timeout' else 'inference_failed'
    else:
        try:
            candidate = parse_candidate(result.text)
        except InvalidDraft:
            result, candidate, failure = result, None, 'invalid_response'
        else:
            failure = None
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before drafting.')
            gate(before)
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (before['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before drafting.', 422)
            reason = failure or validate_citations(cur, retrieved, candidate)
            if reason is None:
                try:
                    body = assemble_grounded(candidate.citations)
                except ValueError:
                    reason = 'invalid_response'
            if reason is None:
                grounding, citations, confidence, after = 'grounded', \
                    [citation.model_dump(mode='json') for citation in candidate.citations], candidate.confidence, before
            else:
                body, grounding, citations, confidence = FIXED_UNSUPPORTED_BODY, 'unsupported', [], None
                after = cur.execute("UPDATE soloestate.conversations SET status='pending_owner' WHERE id=%s RETURNING *",
                                    (identifier,)).fetchone()
                log_activity(cur, before, after, 'Assistant answer needs owner review', request.request_key)
            row = cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,role,body,grounding,unsupported_reason,
                citations,confidence,model_name,response_id,request_key) VALUES (%s,'assistant',%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (identifier, body, grounding, reason, Jsonb(citations), confidence,
                 result.model if result else None, result.response_id if result else None, request.request_key)).fetchone()
            return {'record': row, 'conversation': after}
    return crm.mutate(f'conversation:draft:{identifier}', request, work)


def handoff(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before changing handoff.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (before['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before changing handoff.', 422)
            target = 'requested' if request.action == 'request' else 'resolved'
            if target not in HANDOFF[before['handoff_state']]:
                raise CRMError('Invalid handoff transition.')
            after = cur.execute('UPDATE soloestate.conversations SET handoff_state=%s WHERE id=%s RETURNING *',
                                (target, identifier)).fetchone()
            log_activity(cur, before, after, request.reason.strip(), request.request_key)
            return {'record': after}
    return crm.mutate(f'conversation:handoff:{identifier}', request, work)


def review(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            before = found(cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if before['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before reviewing.')
            lead = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (before['lead_id'],)).fetchone())
            if lead['archived_at'] is not None:
                raise CRMError('Restore the lead before reviewing.', 422)
            target = REVIEW.get((before['status'], request.action))
            if target is None:
                raise CRMError('Invalid review transition.')
            after = cur.execute('UPDATE soloestate.conversations SET status=%s WHERE id=%s RETURNING *', (target, identifier)).fetchone()
            log_activity(cur, before, after, request.reason.strip(), request.request_key)
            return {'record': after}
    return crm.mutate(f'conversation:review:{identifier}', request, work)
