"""Owner-managed versioned FAQs. Immutable versions; retrieval only, no inference here."""
import re
from uuid import uuid4

TOKEN = re.compile(r'[a-z0-9]{3,}')


def tokens(text):
    return set(TOKEN.findall(text.lower()))


def create(crm, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            if request.scope == 'property':
                prop = found(cur.execute('SELECT * FROM soloestate.properties WHERE id=%s', (request.property_id,)).fetchone())
                if not prop['active']:
                    raise CRMError('Property FAQs require an active property.', 422)
            question, answer = request.question.strip(), request.answer.strip()
            faq = cur.execute('INSERT INTO soloestate.faqs(id,scope,property_id) VALUES (%s,%s,%s) RETURNING *',
                              (uuid4(), request.scope, request.property_id)).fetchone()
            cur.execute('INSERT INTO soloestate.faq_versions(faq_id,version,question,answer,request_key) VALUES (%s,1,%s,%s,%s)',
                        (faq['id'], question, answer, request.request_key))
            return {'record': {**faq, 'question': question, 'answer': answer}}
    return crm.mutate('faq:create', request, work)


def listing(crm, *, scope=None, property_id=None, status=None, q='', page=1, page_size=20):
    from app.crm import CRM, CRMError, encode
    from psycopg.rows import dict_row
    if scope is not None and scope not in ('business', 'property'):
        raise CRMError('Invalid FAQ scope.', 422)
    if status is not None and status not in ('active', 'archived'):
        raise CRMError('Invalid FAQ status.', 422)
    conditions, values = [], []
    if scope:
        conditions.append('f.scope=%s')
        values.append(scope)
    if property_id:
        conditions.append('f.property_id=%s')
        values.append(property_id)
    if status:
        conditions.append('f.status=%s')
        values.append(status)
    if q:
        conditions.append('(v.question ILIKE %s OR v.answer ILIKE %s)')
        values.extend([CRM.pattern(q)] * 2)
    clause = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    base = 'soloestate.faqs f JOIN soloestate.faq_versions v ON v.faq_id=f.id AND v.version=f.current_version'
    with crm.connect() as conn:
        total = conn.execute(f'SELECT count(*) FROM {base}{clause}', values).fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(f'SELECT f.*,v.question,v.answer FROM {base}{clause} ORDER BY f.updated_at DESC,f.id LIMIT %s OFFSET %s',
                               [*values, page_size, (page - 1) * page_size]).fetchall()
    return encode(dict(items=rows, total=total, page=page, page_size=page_size))


def detail(crm, identifier):
    from app.crm import encode, found
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            faq = found(cur.execute('SELECT * FROM soloestate.faqs WHERE id=%s', (identifier,)).fetchone())
            versions = cur.execute('SELECT faq_id,version,question,answer,created_at FROM soloestate.faq_versions WHERE faq_id=%s ORDER BY version',
                                   (identifier,)).fetchall()
    current = next(row for row in versions if row['version'] == faq['current_version'])
    return encode({'record': {**faq, 'question': current['question'], 'answer': current['answer']}, 'versions': versions})


def new_version(crm, identifier, request):
    from app.crm import CRMError, found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            faq = found(cur.execute('SELECT * FROM soloestate.faqs WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if faq['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before editing.')
            if faq['status'] != 'active':
                raise CRMError('Restore the FAQ before adding a version.')
            question, answer = request.question.strip(), request.answer.strip()
            version = faq['current_version'] + 1
            cur.execute('INSERT INTO soloestate.faq_versions(faq_id,version,question,answer,request_key) VALUES (%s,%s,%s,%s,%s)',
                        (identifier, version, question, answer, request.request_key))
            row = cur.execute('UPDATE soloestate.faqs SET current_version=%s WHERE id=%s RETURNING *', (version, identifier)).fetchone()
            return {'record': {**row, 'question': question, 'answer': answer}}
    return crm.mutate(f'faq:version:{identifier}', request, work)


def archive(crm, identifier, request):
    from app.crm import found
    from psycopg.rows import dict_row
    def work(conn):
        with conn.cursor(row_factory=dict_row) as cur:
            from app.crm import CRMError
            faq = found(cur.execute('SELECT * FROM soloestate.faqs WHERE id=%s FOR UPDATE', (identifier,)).fetchone())
            if faq['version'] != request.expected_version:
                raise CRMError('Record changed. Reload before archiving.')
            row = cur.execute("UPDATE soloestate.faqs SET status=%s WHERE id=%s RETURNING *",
                              ('archived' if request.archived else 'active', identifier)).fetchone()
            current = cur.execute('SELECT question,answer FROM soloestate.faq_versions WHERE faq_id=%s AND version=%s',
                                  (identifier, row['current_version'])).fetchone()
            return {'record': {**row, 'question': current['question'], 'answer': current['answer']}}
    return crm.mutate(f'faq:archive:{identifier}', request, work)


def retrieve(cur, conversation, text, limit=5):
    rows = cur.execute("""SELECT f.id AS faq_id,f.current_version AS version,f.updated_at,v.question,v.answer
        FROM soloestate.faqs f JOIN soloestate.faq_versions v ON v.faq_id=f.id AND v.version=f.current_version
        WHERE f.status='active' AND (f.scope='business' OR f.property_id=%s)""", (conversation.get('property_id'),)).fetchall()
    words = tokens(text)
    scored = [(len(words & tokens(row['question'] + ' ' + row['answer'])), row) for row in rows]
    scored.sort(key=lambda item: str(item[1]['faq_id']))
    scored.sort(key=lambda item: item[1]['updated_at'], reverse=True)
    scored.sort(key=lambda item: -item[0])
    return [dict(faq_id=row['faq_id'], version=row['version'], question=row['question'], answer=row['answer'])
            for _, row in scored[:limit]]
