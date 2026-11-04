"""D2 summary tests (SUM matrix + context budgets). Rollback by default, except the
committed concurrency cases which clean up by exact IDs."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from uuid import uuid4

import pytest
from psycopg.rows import dict_row

from app import crm_schemas as s
from app import receptionist, summaries
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM, scalar
from app.database import connect
from app.services.meta_model import ModelResult, ModelServiceError

pytestmark = pytest.mark.d2_database


class StubModel:
    def __init__(self, texts=(), error=None, gate=None):
        self.texts = list(texts)
        self.error = error
        self.gate = gate
        self.calls = 0
        self.prompts = []

    async def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        if self.gate is not None:
            self.gate()
        if self.error is not None:
            raise self.error
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


@pytest.fixture
def sx(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2sx-' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
                       key=lambda: prefix + ':' + str(uuid4()))


def _lead(b, **kw):
    base = dict(request_key=b['key'](), name=b['prefix'], email=b['prefix'] + '@example.invalid')
    base.update(kw)
    return b['crm'].save('leads', s.LeadCreate(**base))['record']


def _convo(conn, lead_id, key):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id,channel) '
                           'VALUES (%s,%s,%s,%s) RETURNING *',
                           (uuid4(), lead_id, None, 'whatsapp')).fetchone()


def _fill(conn, convo_id, count, prefix, start_seq=1, role='customer'):
    ids = []
    with conn.cursor() as cur:
        for number in range(start_seq, start_seq + count):
            op_key = '%s:msg:%d:%s' % (prefix, number, uuid4())
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,response) '
                        'VALUES (%s,%s,%s,%s,%s)', (op_key, 'test:fill', op_key, 'owner', '{}'))
            row = cur.execute('INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s) RETURNING id',
                              (convo_id, number, role, 'Fictional message %d' % number, op_key)).fetchone()
            ids.append(row[0])
    return ids


def _summary_json(body='Fictional summary of requirements.', facts=None):
    return json.dumps({'summary': body, 'facts': facts if facts is not None else {'need': '2bhk'}})


def test_ctx_no_summary_is_complete(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 5, sx['prefix'])
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        ctx = summaries.context(cur, convo['id'])
    assert ctx['summary'] is None and ctx['through_seq'] == 0 and ctx['tail_truncated'] is False
    assert [m['seq'] for m in ctx['messages']] == [1, 2, 3, 4, 5]


def test_ctx_overflow_truncates_tail(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 35, sx['prefix'])
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        ctx = summaries.context(cur, convo['id'])
    assert ctx['tail_truncated'] is True and len(ctx['messages']) == 30
    assert ctx['messages'][0]['seq'] == 1


def test_ctx_boundary_respected(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 35, sx['prefix'])
    with sx['conn'].cursor() as cur:
        cur.execute('INSERT INTO soloestate.conversation_summaries(conversation_id,through_seq,body,request_key) '
                    'VALUES (%s,%s,%s,%s)', (convo['id'], 20, 'Fictional boundary', sx['key']()))
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        ctx = summaries.context(cur, convo['id'])
    assert ctx['through_seq'] == 20 and ctx['tail_truncated'] is False
    assert [m['seq'] for m in ctx['messages']] == list(range(21, 36))


def test_gen_covers_through_max_minus_ten(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 35, sx['prefix'])
    stub = StubModel([_summary_json()])
    result = summaries.generate(sx['factory'], convo['id'], stub)
    assert result == {'generated': True, 'replayed': False, 'through_seq': 25}
    assert stub.calls == 1 and 'Fictional message 1' in stub.prompts[0]
    assert 'Fictional message 25' in stub.prompts[0] and 'Fictional message 26' not in stub.prompts[0]
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        ctx = summaries.context(cur, convo['id'], keep_messages=summaries.TAIL_KEEP_AFTER_SUMMARY,
                                keep_bytes=summaries.TAIL_KEEP_BYTES)
    assert ctx['summary']['through_seq'] == 25 and ctx['tail_truncated'] is False
    assert [m['seq'] for m in ctx['messages']] == list(range(26, 36))


def test_gen_invalid_output_fails_without_row(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 35, sx['prefix'])
    assert summaries.generate(sx['factory'], convo['id'], StubModel(['not json']))['generated'] is False
    big = json.dumps({'summary': 'x', 'facts': {'k': 'v' * 5000}})
    assert summaries.generate(sx['factory'], convo['id'], StubModel([big]))['generated'] is False
    with sx['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                                  (convo['id'],)).fetchone()) == 0


def test_gen_noop_without_overflow(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 35, sx['prefix'])
    first = summaries.generate(sx['factory'], convo['id'], StubModel([_summary_json()]))
    assert first == {'generated': True, 'replayed': False, 'through_seq': 25}
    second = summaries.generate(sx['factory'], convo['id'], StubModel([_summary_json('Other body')]))
    assert second == {'generated': False, 'reason': 'no_overflow'}
    with sx['conn'].cursor() as cur:
        rows = cur.execute('SELECT body FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                           (convo['id'],)).fetchall()
        assert len(rows) == 1 and 'requirements' in rows[0][0]


def test_sum03_degraded_turn_clarifies(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 34, sx['prefix'])
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        op_key = sx['key']()
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,response) '
                    'VALUES (%s,%s,%s,%s,%s)', (op_key, 'test:fill', op_key, 'owner', '{}'))
        message = cur.execute('INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,request_key) '
                              "VALUES (%s,35,'customer',%s,%s) RETURNING *",
                              (convo['id'], 'Do you charge a buyer fee?', op_key)).fetchone()
    failing = StubModel(error=ModelServiceError('timeout', 'Fictional timeout'))
    result = receptionist.respond_turn(sx['factory'], convo['id'], message['id'], model=failing)
    assert result['replied'] is True and failing.calls == 1
    assert 'little more' in result['payload'] and result['grounding'] == 'validated'
    assert result['context_degraded'] is True
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        evidence = cur.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',
                               ('channel:reply:' + str(message['id']),)).fetchone()['response']
        assert evidence['context_degraded'] is True
        activities = cur.execute('SELECT reason,actor FROM soloestate.lead_activities WHERE lead_id=%s '
                                 'ORDER BY created_at', (lead['id'],)).fetchall()
        assert {'reason': 'Turn ran on degraded context; clarified instead of asserting',
                'actor': 'channel'} in [dict(a) for a in activities]
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                                  (convo['id'],)).fetchone()) == 0
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.conversations WHERE id=%s',
                           (convo['id'],)).fetchone()['status'] == 'open'


def test_sum_turn_uses_summary_after_generation(sx):
    lead = _lead(sx)
    convo = _convo(sx['conn'], lead['id'], sx['key'])
    _fill(sx['conn'], convo['id'], 34, sx['prefix'])
    with sx['conn'].cursor(row_factory=dict_row) as cur:
        op_key = sx['key']()
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,response) '
                    'VALUES (%s,%s,%s,%s,%s)', (op_key, 'test:fill', op_key, 'owner', '{}'))
        message = cur.execute('INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,request_key) '
                              "VALUES (%s,35,'customer','Hello',%s) RETURNING *", (convo['id'], op_key)).fetchone()
    turn = json.dumps(dict(catalog_version=CATALOG_VERSION, category='welcome',
                           segments=[dict(phrase_id='ACK_RECEIVED', args={})]))
    stub = StubModel([_summary_json('Customer asked about fees.'), turn])
    result = receptionist.respond_turn(sx['factory'], convo['id'], message['id'], model=stub)
    assert result['replied'] is True and result['grounding'] == 'validated'
    assert stub.calls == 2 and 'Customer asked about fees.' in stub.prompts[1]
    assert result.get('context_degraded') is None
    with sx['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT through_seq FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                                  (convo['id'],)).fetchone()) == 25


@pytest.fixture
def sxc(request):
    """Committed fixture for true summary concurrency; exact-ID cleanup."""
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    prefix = 'd2sxc-' + str(uuid4())
    data = dict(prefix=prefix, key=lambda: prefix + ':' + str(uuid4()), ids={})
    crm = CRM()
    lead = crm.save('leads', s.LeadCreate(request_key=data['key'](), name=prefix,
                                          email=prefix + '@example.invalid'))['record']
    data['ids']['lead'] = lead['id']
    with connect(Settings()) as conn:
        convo = _convo(conn, lead['id'], data['key'])
        data['ids']['convo'] = convo['id']
        data['ids']['messages'] = _fill(conn, convo['id'], 35, prefix)
    yield data
    with connect(Settings()) as conn:
        with conn.transaction():
            conn.execute('DELETE FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                         (data['ids']['convo'],))
            conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id=%s',
                         (data['ids']['convo'],))
            conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (data['ids']['convo'],))
            conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (data['ids']['lead'],))
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key LIKE %s ESCAPE %s',
                         (prefix.replace('%', '\\%').replace('_', '\\_') + '%', '\\'))
            conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (data['ids']['lead'],))


def _fresh_factory():
    return connect(Settings())


def test_sum01_concurrent_generation_single_row(sxc):
    barrier = Barrier(2)
    stub = StubModel([_summary_json(), _summary_json('Second body')])
    gate_calls = []
    original = stub.generate

    async def gated(prompt):
        gate_calls.append(1)
        barrier.wait(timeout=120)
        return await original(prompt)

    stub.generate = gated
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: summaries.generate(_fresh_factory, sxc['ids']['convo'], stub), (0, 1)))
    assert sorted(r['through_seq'] for r in results) == [25, 25]
    assert sorted(r['generated'] for r in results) == [True, True]
    assert sorted(r['replayed'] for r in results) == [False, True]
    with connect(Settings()) as conn:
        rows = conn.execute('SELECT through_seq, body FROM soloestate.conversation_summaries WHERE conversation_id=%s',
                            (sxc['ids']['convo'],)).fetchall()
        assert len(rows) == 1 and rows[0][0] == 25
        covered = conn.execute('SELECT count(*) FROM soloestate.conversation_messages WHERE conversation_id=%s '
                               'AND seq>%s', (sxc['ids']['convo'], rows[0][0])).fetchone()[0]
        assert covered == 10


def test_sum02_appends_during_generation_are_covered(sxc):
    entered = Event()
    release = Event()

    async def slow(prompt):
        entered.set()
        assert release.wait(timeout=120)
        return ModelResult(response_id='resp-slow', model='test-meta', text=_summary_json())

    stub = StubModel()
    stub.generate = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(summaries.generate, _fresh_factory, sxc['ids']['convo'], stub)
        assert entered.wait(timeout=120)
        with connect(Settings()) as conn:
            with conn.cursor() as cur:
                top = cur.execute('SELECT max(seq) FROM soloestate.conversation_messages WHERE conversation_id=%s',
                                  (sxc['ids']['convo'],)).fetchone()[0]
                for number in (top + 1, top + 2, top + 3):
                    op_key = sxc['key']()
                    cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,response) '
                                'VALUES (%s,%s,%s,%s,%s)', (op_key, 'test:fill', op_key, 'owner', '{}'))
                    cur.execute('INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,request_key) '
                                'VALUES (%s,%s,%s,%s,%s)', (sxc['ids']['convo'], number, 'customer',
                                                           'Fictional late %d' % number, op_key))
        release.set()
        result = future.result(timeout=120)
    assert result['generated'] is True and result['through_seq'] == 25
    with connect(Settings()) as conn:
        seqs = [row[0] for row in conn.execute(
            'SELECT seq FROM soloestate.conversation_messages WHERE conversation_id=%s ORDER BY seq',
            (sxc['ids']['convo'],)).fetchall()]
        assert seqs == list(range(1, 39))
        tail = [row[0] for row in conn.execute(
            'SELECT seq FROM soloestate.conversation_messages WHERE conversation_id=%s AND seq>%s ORDER BY seq',
            (sxc['ids']['convo'], result['through_seq'])).fetchall()]
        assert tail == list(range(26, 39))
