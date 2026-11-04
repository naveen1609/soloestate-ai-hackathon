"""Conversation memory: seq-ordered context retrieval over rolling summaries.

The summary boundary is commit-ordered (`through_seq` on the append-only
`conversation_summaries` table), so no committed message can hide behind
a boundary the way `(created_at, id)` ordering would allow. Summaries
are untrusted context: the CRM stays the source of truth for current
properties, prices, appointments and policies, and missing context
never authorizes actions.
"""
TAIL_MESSAGE_LIMIT = 30
TAIL_BYTES_LIMIT = 12 * 1024
TAIL_KEEP_AFTER_SUMMARY = 10
TAIL_KEEP_BYTES = 8 * 1024
GENERATION_MESSAGE_LIMIT = 50
GENERATION_BYTES_LIMIT = 24 * 1024
MAX_FACT_KEYS = 20
MAX_FACT_CHARS = 200


def context(cur, conversation_id, *, keep_messages=None, keep_bytes=None):
    """Latest summary plus every message after its boundary, seq-ordered.

    Returns {'summary': row|None, 'through_seq': int, 'messages': [...],
    'tail_truncated': bool}. When no summary exists the tail starts at
    seq 1, which is complete context rather than degraded context. After
    a successful regeneration the caller trims to the last 10 / 8KB.
    """
    message_cap = keep_messages or TAIL_MESSAGE_LIMIT
    byte_cap = keep_bytes or TAIL_BYTES_LIMIT
    summary = cur.execute("""SELECT through_seq, body, facts FROM soloestate.conversation_summaries
        WHERE conversation_id=%s ORDER BY through_seq DESC, created_at DESC LIMIT 1""",
                          (conversation_id,)).fetchone()
    through = summary['through_seq'] if summary else 0
    rows = cur.execute("""SELECT seq, role, body, grounding, unsupported_reason, citations
        FROM soloestate.conversation_messages WHERE conversation_id=%s AND seq>%s
        ORDER BY seq LIMIT %s""", (conversation_id, through, message_cap + 1)).fetchall()
    truncated = len(rows) > message_cap
    rows = rows[:message_cap]
    total = 0
    kept = []
    for row in rows:
        total += len(row['body'] or '')
        if total > byte_cap:
            truncated = True
            break
        kept.append(row)
    return {'summary': dict(summary) if summary else None, 'through_seq': through,
            'messages': [dict(row) for row in kept], 'tail_truncated': truncated}


def summary_prompt(rows):
    """Bounded summarization prompt; messages are untrusted context."""
    lines = ['Summarize this customer conversation for the receptionist. Return one JSON object, no Markdown: '
             '{"summary": string 1-2000 chars, "facts": {key: short string, at most 20 keys}}. '
             'Capture stated requirements, decisions, pending items, and open questions. '
             'Never invent requirements or commitments.',
             '--- untrusted messages ---']
    total = 0
    for row in rows[:GENERATION_MESSAGE_LIMIT]:
        chunk = '%s: %s' % (row['role'], (row['body'] or '')[:1000])
        total += len(chunk)
        if total > GENERATION_BYTES_LIMIT:
            break
        lines.append(chunk)
    return '\n'.join(lines)


def parse_summary(text):
    """Strict validation: bounded body + small string-valued fact object."""
    import json
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError('summary is not JSON: %s' % exc) from None
    if not isinstance(parsed, dict):
        raise ValueError('summary must be an object')
    body = parsed.get('summary')
    facts = parsed.get('facts')
    if not isinstance(body, str) or not (1 <= len(body) <= 2000):
        raise ValueError('summary body must be 1-2000 characters')
    if not isinstance(facts, dict) or len(facts) > MAX_FACT_KEYS:
        raise ValueError('summary facts must be an object with at most %d keys' % MAX_FACT_KEYS)
    for key, value in facts.items():
        if not isinstance(key, str) or not isinstance(value, str) or len(value) > MAX_FACT_CHARS:
            raise ValueError('summary facts must map strings to short strings')
    if len(json.dumps(facts, sort_keys=True)) > 4096:
        raise ValueError('summary facts exceed 4KB')
    return body, facts


def _run_generate(model, prompt):
    import asyncio
    result = model.generate(prompt)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def generate(conn_factory, conversation_id, model):
    """Summarize-before-truncate under the conversation lock.

    Covers (boundary, max-10] so the prompt keeps its last 10 messages,
    or everything new when ten or fewer messages are uncovered (byte
    overflow with a short tail). Concurrent same-boundary generation
    collapses onto one row via the (conversation_id, through_seq) unique
    + request-key replay. Returns {'generated', 'through_seq', ...};
    failures return generated=False and the caller degrades instead of
    truncating required context.
    """
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (conversation_id,))
            boundary = cur.execute("""SELECT coalesce(max(through_seq),0) AS through
                FROM soloestate.conversation_summaries WHERE conversation_id=%s""",
                                     (conversation_id,)).fetchone()['through']
            top = cur.execute('SELECT coalesce(max(seq),0) AS top FROM soloestate.conversation_messages '
                              'WHERE conversation_id=%s', (conversation_id,)).fetchone()['top']
            if top <= boundary:
                return {'generated': False, 'reason': 'nothing_to_cover'}
            tail = cur.execute("""SELECT count(*) AS n, coalesce(sum(length(body)),0) AS bytes
                FROM soloestate.conversation_messages WHERE conversation_id=%s AND seq>%s""",
                               (conversation_id, boundary)).fetchone()
            if tail['n'] <= TAIL_MESSAGE_LIMIT and tail['bytes'] <= TAIL_BYTES_LIMIT:
                return {'generated': False, 'reason': 'no_overflow'}
            through = top - TAIL_KEEP_AFTER_SUMMARY if top - boundary > TAIL_KEEP_AFTER_SUMMARY else top
            rows = cur.execute("""SELECT seq, role, body FROM soloestate.conversation_messages
                WHERE conversation_id=%s AND seq>%s AND seq<=%s ORDER BY seq""",
                               (conversation_id, boundary, through)).fetchall()
    if not rows:
        return {'generated': False, 'reason': 'nothing_to_cover'}
    try:
        result = _run_generate(model, summary_prompt([dict(row) for row in rows]))
        body, facts = parse_summary(result.text)
    except Exception as exc:  # noqa: BLE001 - any generation failure degrades, never truncates blindly
        return {'generated': False, 'reason': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
    key = 'summary:%s:%d' % (conversation_id, through)
    with conn_factory() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s FOR UPDATE', (conversation_id,))
            existing = cur.execute('SELECT * FROM soloestate.conversation_summaries WHERE request_key=%s',
                                   (key,)).fetchone()
            if existing is not None:
                return {'generated': True, 'replayed': True, 'through_seq': existing['through_seq']}
            latest = cur.execute("""SELECT coalesce(max(through_seq),0) AS through
                FROM soloestate.conversation_summaries WHERE conversation_id=%s""",
                                   (conversation_id,)).fetchone()['through']
            if latest >= through:
                row = cur.execute("""SELECT * FROM soloestate.conversation_summaries WHERE conversation_id=%s
                    ORDER BY through_seq DESC LIMIT 1""", (conversation_id,)).fetchone()
                return {'generated': True, 'replayed': True, 'through_seq': row['through_seq']}
            try:
                with conn.transaction():
                    row = cur.execute("""INSERT INTO soloestate.conversation_summaries(conversation_id,through_seq,
                        body,facts,request_key) VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                                      (conversation_id, through, body, Jsonb(facts), key)).fetchone()
            except Exception:  # unique race lost: re-read the winner's row
                row = cur.execute('SELECT * FROM soloestate.conversation_summaries WHERE request_key=%s',
                                  (key,)).fetchone()
                if row is None:
                    raise
                return {'generated': True, 'replayed': True, 'through_seq': row['through_seq']}
            return {'generated': True, 'replayed': False, 'through_seq': row['through_seq']}
