"""Channel identity isolation and owner-verified linking. D1: schema, owner actions,
isolation evaluator; adapters consume it in D2. Attested senders prove channel
control, never lead ownership: new identities always start isolated.
"""
import re
from uuid import uuid4

OPTOUT_KEYWORDS = frozenset({'STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT'})


def match_optout_keyword(text):
    return bool(text and text.strip()) and text.strip().upper() in OPTOUT_KEYWORDS


def normalize_sender(channel, provider_ref):
    """Channel-defined sender normalization for in-thread destination binding.

    WhatsApp wa_id digits map to +E.164 (the same rule link verification
    enforces); website handles are opaque-exact. Returns None when unmappable,
    which never equals a real destination, so unmappable callers fail closed.
    """
    if channel == 'whatsapp' and provider_ref and provider_ref.isdigit():
        return '+' + provider_ref
    if channel == 'website' and provider_ref and provider_ref.startswith('web:'):
        return provider_ref
    return None


def _isolated_email(channel, provider_ref):
    local = re.sub(r'[^a-z0-9]+', '-', f'{channel}-{provider_ref}'.lower()).strip('-')
    return f'{local}-{uuid4().hex[:8]}@example.invalid'


def _open_conversation(cur, lead_id):
    row = cur.execute("""SELECT id FROM soloestate.conversations WHERE lead_id=%s AND status='open'
        ORDER BY created_at DESC, id DESC LIMIT 1""", (lead_id,)).fetchone()
    if row:
        return row['id'], False
    return cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING id',
                       (uuid4(), lead_id, None)).fetchone()['id'], True


def observe(cur, channel, provider_ref, request_key, actor='channel'):
    """Resolve (channel, provider_ref) to its live link, minting an observed link
    with an isolated lead + conversation on first sight. Returns (link, created)."""
    from app.crm import CRMError
    from app.qualification import activity
    if channel not in ('website', 'whatsapp') or not provider_ref or not provider_ref.strip():
        raise CRMError('Channel identity is invalid.', 422)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'identity:{channel}:{provider_ref}',))
    live = cur.execute("""SELECT * FROM soloestate.identity_links
        WHERE channel=%s AND provider_ref=%s AND status IN ('observed','active')""",
        (channel, provider_ref)).fetchone()
    if live:
        return live, False
    lead = cur.execute('INSERT INTO soloestate.leads(id,name,email,notes) VALUES (%s,%s,%s,%s) RETURNING *',
                       (uuid4(), f'{channel} visitor', _isolated_email(channel, provider_ref),
                        f'Isolated {channel} identity: {provider_ref}')).fetchone()
    convo = cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING *',
                        (uuid4(), lead['id'], None)).fetchone()
    link = cur.execute("""INSERT INTO soloestate.identity_links(id,channel,provider_ref,lead_id,status,created_by_actor)
        VALUES (%s,%s,%s,%s,'observed',%s) RETURNING *""",
        (uuid4(), channel, provider_ref, lead['id'], actor)).fetchone()
    activity(cur, lead, lead, f'Isolated {channel} identity observed', request_key, actor
             if actor in ('owner', 'visitor', 'channel', 'policy') else 'owner')
    return link, True


def receive(crm, channel, provider_ref, request_key):
    """D1 synthetic-inbound entry: first sight isolates; live links route.
    Active links resolve the canonical conversation; nothing here sends."""
    from app.crm import encode
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            link, created = observe(cur, channel, provider_ref, request_key)
            if link['status'] == 'active':
                conversation_id, _ = _open_conversation(cur, link['lead_id'])
                scope = 'canonical'
            else:
                conversation_id, _ = _open_conversation(cur, link['lead_id'])
                scope = 'isolated'
            return {'record': link, 'lead_id': link['lead_id'], 'conversation_id': conversation_id,
                    'scope': scope, 'created': created}
    response = crm.mutate('identity-link:observe', _Key(request_key), work)
    return encode(response)


class _Key:
    # Minimal request_key carrier for service-driven observe calls.
    def __init__(self, request_key):
        self.request_key = request_key
        self.model_fields_set = {'request_key'}

    def model_dump(self, mode='json'):
        return {'request_key': self.request_key}


def _validate_destination(channel, provider_ref, destination):
    from app.crm import CRMError
    if channel == 'whatsapp':
        if not provider_ref.isdigit() or destination != '+' + provider_ref:
            raise CRMError('WhatsApp verified destination must equal the normalized sender identity.', 422)
    elif '@' in destination:
        if ' ' in destination or destination.count('@') != 1:
            raise CRMError('Verified destination must be an email address or E.164 phone.', 422)
    else:
        from app.crm_schemas import PHONE_PATTERN
        if not re.fullmatch(PHONE_PATTERN, destination):
            raise CRMError('Verified destination must be an email address or E.164 phone.', 422)


def link(crm, request):
    from app.crm import CRMError, found
    from app.qualification import activity
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            observed = found(cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s FOR UPDATE',
                                         (request.link_id,)).fetchone())
            if observed['status'] != 'observed':
                raise CRMError('Only an observed identity can be linked.')
            canonical = found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE', (request.lead_id,)).fetchone())
            if canonical['archived_at'] is not None:
                raise CRMError('Link to an archived lead is not allowed.', 422)
            if canonical['id'] == observed['lead_id']:
                raise CRMError('This identity is already isolated to that lead.', 422)
            _validate_destination(observed['channel'], observed['provider_ref'], request.verified_destination)
            cur.execute("UPDATE soloestate.identity_links SET status='superseded',decided_at=clock_timestamp() WHERE id=%s",
                        (observed['id'],))
            active = cur.execute("""INSERT INTO soloestate.identity_links
                (id,channel,provider_ref,lead_id,verified_destination,method,evidence,status,created_by_actor,decided_at)
                VALUES (%s,%s,%s,%s,%s,'owner_verified',%s,'active','owner',clock_timestamp()) RETURNING *""",
                (uuid4(), observed['channel'], observed['provider_ref'], canonical['id'],
                 request.verified_destination, Jsonb({'note': request.reason.strip()}))).fetchone()
            conversation_id, _ = _open_conversation(cur, canonical['id'])
            cur.execute('UPDATE soloestate.leads SET archived_at=clock_timestamp() WHERE id=%s',
                        (observed['lead_id'],))
            activity(cur, canonical, canonical, 'Identity linked: ' + request.reason.strip(), request.request_key)
            return {'record': active, 'canonical_conversation_id': conversation_id}
    return crm.mutate('identity-link:link', request, work)


def revoke(crm, identifier, request):
    from app.crm import CRMError, found
    from app.qualification import activity
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            link = found(cur.execute('SELECT * FROM soloestate.identity_links WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if link['version'] != request.expected_version:
                raise CRMError('Link changed. Reload before revoking.')
            if link['status'] != 'active':
                raise CRMError('Only an active link can be revoked.')
            updated = cur.execute("""UPDATE soloestate.identity_links SET status='revoked',decided_at=clock_timestamp()
                WHERE id=%s RETURNING *""", (identifier,)).fetchone()
            lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
            activity(cur, lead, lead, 'Identity link revoked: ' + request.reason.strip(), request.request_key)
            return {'record': updated}
    return crm.mutate('identity-link:revoke', request, work)


def listing(crm, *, lead_id=None, status=None, page=1, page_size=20):
    from app.crm import CRMError, encode
    from psycopg.rows import dict_row
    if status is not None and status not in ('observed', 'active', 'revoked', 'superseded'):
        raise CRMError('Invalid link status.', 422)
    conditions, values = [], []
    if lead_id is not None:
        conditions.append('lead_id=%s')
        values.append(lead_id)
    if status is not None:
        conditions.append('status=%s')
        values.append(status)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM soloestate.identity_links{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f'SELECT * FROM soloestate.identity_links{clause} ORDER BY created_at DESC, id LIMIT %s OFFSET %s',
                               [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))


def suppress(crm, request):
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("""INSERT INTO soloestate.suppression_list(channel,destination,source,evidence_ref)
                VALUES (%s,%s,'owner_action',%s) ON CONFLICT DO NOTHING RETURNING *, (xmax = 0) AS created""",
                (request.channel, request.destination, request.evidence_ref)).fetchone()
            if row is None:
                existing = cur.execute('SELECT *, false AS created FROM soloestate.suppression_list WHERE channel=%s AND destination=%s',
                                       (request.channel, request.destination)).fetchone()
                return {'record': existing}
            return {'record': row}
    return crm.mutate('suppression:create', request, work)


def is_suppressed(cur, channel, destination):
    from app.crm import scalar
    return scalar(cur.execute('SELECT EXISTS(SELECT 1 FROM soloestate.suppression_list WHERE channel=%s AND destination=%s)',
                              (channel, destination)).fetchone())


def suppression_listing(crm):
    from app.crm import encode
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute('SELECT * FROM soloestate.suppression_list ORDER BY created_at DESC').fetchall()
    items = [dict(row) for row in rows]
    return encode({'items': items, 'total': len(items), 'page': 1, 'page_size': len(items)})
