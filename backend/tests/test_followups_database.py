"""B4 real PostgreSQL follow-up task checks; fictional rollback records, except race fixtures committed on separate connections and deleted by exact ID."""
from contextlib import contextmanager
import threading
import time
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.database import connect
from app.crm import CRM, CRMError
from app.crm_schemas import Archive, LeadCreate, PropertyCreate, PropertyUpdate, TaskCreate, TaskUpdate
from app.followups import create_task, detail, listing, update_task
from app.journey import JourneyUpdate, update as journey_update
from app.main import create_app
from app.crm_api import service
from app.owner_api import require_owner

pytestmark = pytest.mark.integrity_database

PAST = '2026-01-01T00:00:00+00:00'
FUTURE = '2034-01-01T00:00:00+00:00'


@pytest.fixture
def b4(request):
    if not request.config.getoption('--run-integrity-db-tests'): pytest.skip('Use --run-integrity-db-tests')
    committed = []
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction(): yield conn
            crm = CRM(factory); prefix = 'B4-fictional-' + str(uuid4())
            lead = crm.save('leads', LeadCreate(request_key=str(uuid4()), name=prefix, email=prefix + '@example.invalid'))['record']
            prop = crm.save('properties', PropertyCreate(request_key=str(uuid4()), title=prefix, locality=prefix, price=150, bedrooms=2))['record']
            yield dict(crm=crm, conn=conn, lead=lead, prop=prop, prefix=prefix, key=lambda: str(uuid4()), committed=committed)
    for lead_id, prop_id, keys in committed:
        with connect(Settings()) as cleanup_conn:
            cleanup_conn.execute('DELETE FROM soloestate.followup_tasks WHERE lead_id=%s', (lead_id,))
            cleanup_conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (lead_id,))
            if prop_id is not None:
                cleanup_conn.execute('DELETE FROM soloestate.properties WHERE id=%s', (prop_id,))
            for key in keys:
                cleanup_conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (key,))


def make_task(b, **kw):
    base = dict(request_key=b['key'](), lead_id=b['lead']['id'], title='Call buyer about Whitefield')
    return create_task(b['crm'], TaskCreate(**{**base, **kw}))['record']


def edit(b, task, **kw):
    base = dict(request_key=b['key'](), expected_version=task['version'], reason='Fictional task edit')
    return update_task(b['crm'], task['id'], TaskUpdate(**{**base, **kw}))['record']


def client(b):
    app = create_app(); app.dependency_overrides[require_owner] = lambda: 'owner'; app.dependency_overrides[service] = lambda: b['crm']
    return TestClient(app)


def test_create_detail_listing_and_due_ordering(b4):
    b = b4
    past = make_task(b, due_at=PAST); future = make_task(b, due_at=FUTURE); undated = make_task(b)
    assert (past['status'], past['version'], past['assignee'], past['outcome']) == ('open', 1, 'owner', None)
    found = detail(b['crm'], past['id'])
    assert found['record']['id'] == past['id'] and found['record']['overdue'] is True
    assert [entry['reason'] for entry in found['history']] == ['Owner opened task']
    assert found['history'][0]['after_state']['task']['status'] == 'open'
    page = listing(b['crm'], lead_id=b['lead']['id'])
    assert page['total'] == 3
    assert [row['id'] for row in page['items']] == [past['id'], future['id'], undated['id']]
    assert [row['overdue'] for row in page['items']] == [True, False, False]
    assert listing(b['crm'], lead_id=b['lead']['id'], status='open')['total'] == 3
    assert listing(b['crm'], lead_id=b['lead']['id'], status='done')['total'] == 0
    assert listing(b['crm'], lead_id=uuid4())['total'] == 0
    with pytest.raises(CRMError) as exc:
        detail(b['crm'], uuid4())
    assert exc.value.status == 404


def test_property_context_requires_an_active_property(b4):
    b = b4
    assert make_task(b, property_id=b['prop']['id'])['property_id'] == b['prop']['id']
    b['crm'].save('properties', PropertyUpdate(request_key=b['key'](), expected_version=1, title=b['prefix'],
        locality=b['prefix'], price=150, bedrooms=2, active=False), b['prop']['id'])
    with pytest.raises(CRMError) as exc:
        make_task(b, property_id=b['prop']['id'])
    assert exc.value.status == 422
    with pytest.raises(CRMError) as exc:
        make_task(b, property_id=uuid4())
    assert exc.value.status == 404


def test_content_edits_bump_version_with_explicit_null_semantics(b4):
    b = b4
    task = make_task(b, notes='Bring rate sheet.', due_at=FUTURE, property_id=b['prop']['id'], assignee='Fictional aide')
    task = edit(b, task, title='Call buyer about rate sheet')
    assert (task['title'], task['version']) == ('Call buyer about rate sheet', 2)
    assert (task['notes'], task['assignee'], task['property_id']) == ('Bring rate sheet.', 'Fictional aide', b['prop']['id'])
    assert task['due_at'].startswith('2034-01-01')
    task = edit(b, task, due_at=None)
    assert (task['due_at'], task['version']) == (None, 3)
    task = edit(b, task, assignee='owner')
    assert (task['assignee'], task['due_at'], task['version']) == ('owner', None, 4)
    task = edit(b, task, property_id=None)
    assert (task['property_id'], task['version']) == (None, 5)
    with pytest.raises(CRMError, match='No task change'):
        edit(b, task, title='Call buyer about rate sheet')


def test_full_lifecycle_walk_records_outcome(b4):
    b = b4
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    assert (task['status'], task['version']) == ('in_progress', 2)
    task = edit(b, task, status='in_review')
    assert task['status'] == 'in_review'
    with pytest.raises(CRMError, match='outcome'):
        edit(b, task, status='done')
    task = edit(b, task, status='done', outcome='Buyer confirmed a Saturday visit.')
    assert (task['status'], task['outcome'], task['version']) == ('done', 'Buyer confirmed a Saturday visit.', 4)
    with pytest.raises(CRMError, match='terminal'):
        edit(b, task, notes='Late note.')
    with pytest.raises(CRMError, match='terminal'):
        edit(b, task, status='open')
    history = detail(b['crm'], task['id'])['history']
    assert history[0]['after_state']['task'] == {'id': task['id'], 'lead_id': b['lead']['id'], 'property_id': None,
        'title': 'Call buyer about Whitefield', 'notes': '', 'due_at': None, 'assignee': 'owner',
        'status': 'done', 'outcome': 'Buyer confirmed a Saturday visit.', 'version': 4}
    assert history[0]['before_state']['task']['status'] == 'in_review'
    assert history[0]['before_state']['task']['outcome'] is None


def test_invalid_edges_cancel_and_reopen(b4):
    b = b4
    task = make_task(b)
    with pytest.raises(CRMError, match='Invalid task transition'):
        edit(b, task, status='done')
    with pytest.raises(CRMError, match='Invalid task transition'):
        edit(b, task, status='in_review')
    with pytest.raises(CRMError, match='Outcome is recorded only'):
        edit(b, task, status='in_progress', outcome='Too early.')
    task = edit(b, task, status='in_progress')
    with pytest.raises(CRMError, match='Invalid task transition'):
        edit(b, task, status='done', outcome='Skipped review.')
    task = edit(b, task, status='cancelled')
    assert (task['status'], task['outcome']) == ('cancelled', None)
    with pytest.raises(CRMError, match='Invalid task transition'):
        edit(b, task, status='done', outcome='Closed without reopen.')
    with pytest.raises(CRMError, match='Reopen'):
        edit(b, task, notes='Edit while cancelled.')
    task = edit(b, task, status='open')
    assert (task['status'], task['outcome']) == ('open', None)


def test_content_edits_invalidate_pending_review(b4):
    b = b4
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    task = edit(b, task, status='in_review')
    task = edit(b, task, title='Call buyer about revised rate sheet')
    assert (task['status'], task['title'], task['version']) == ('in_progress', 'Call buyer about revised rate sheet', 4)
    task = edit(b, task, status='in_review')
    assert (task['status'], task['version']) == ('in_review', 5)
    with pytest.raises(CRMError, match='separate operations'):
        edit(b, task, title='Another revision', status='done', outcome='Combined edit and approval.')
    assert detail(b['crm'], task['id'])['record']['status'] == 'in_review'
    task = edit(b, task, status='done', outcome='Reviewed after the final revision.')
    assert (task['status'], task['version']) == ('done', 6)


@pytest.mark.parametrize('field,value', [
    ('title', 'Call buyer about revised rates'),
    ('notes', 'Bring the revised rate sheet.'),
    ('due_at', FUTURE),
    ('assignee', 'Fictional aide'),
    ('property_id', None),
])
def test_each_content_field_invalidates_pending_review(b4, field, value):
    b = b4
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    task = edit(b, task, status='in_review')
    if field == 'property_id':
        value = b['prop']['id']
    task = edit(b, task, **{field: value})
    assert (task['status'], task['version']) == ('in_progress', 4)


@pytest.mark.parametrize('extra', [
    dict(status='in_review'),
    dict(status='done', outcome='Combined edit and approval.'),
])
def test_combined_content_and_review_requests_rejected_without_side_effects(b4, extra):
    b = b4
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    task = edit(b, task, status='in_review')
    before = detail(b['crm'], task['id'])
    key = b['key']()
    with pytest.raises(CRMError, match='separate operations'):
        update_task(b['crm'], task['id'], TaskUpdate(request_key=key, expected_version=task['version'],
            reason='Fictional combined', title='Call buyer about revised rates', **extra))
    assert detail(b['crm'], task['id']) == before
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404


def test_unchanged_content_does_not_invalidate_review(b4):
    b = b4
    task = make_task(b, notes='Bring rate sheet.', due_at=FUTURE, assignee='Fictional aide')
    task = edit(b, task, status='in_progress')
    task = edit(b, task, status='in_review')
    task = edit(b, task, title=task['title'], notes='Bring rate sheet.', due_at=FUTURE, assignee='Fictional aide',
        status='done', outcome='Reviewed without new edits.')
    assert (task['status'], task['version']) == ('done', 4)


def test_replay_returns_identical_task_and_conflicts_on_reuse(b4):
    b = b4
    req = TaskCreate(request_key=b['key'](), lead_id=b['lead']['id'], title='Call buyer')
    first = create_task(b['crm'], req)['record']
    assert create_task(b['crm'], req)['record'] == first
    assert listing(b['crm'], lead_id=b['lead']['id'])['total'] == 1
    req = TaskUpdate(request_key=b['key'](), expected_version=1, status='in_progress', reason='Fictional start')
    assert update_task(b['crm'], first['id'], req)['record'] == update_task(b['crm'], first['id'], req)['record']
    assert detail(b['crm'], first['id'])['record']['version'] == 2
    with pytest.raises(CRMError, match='already belongs'):
        update_task(b['crm'], first['id'], req.model_copy(update={'status': 'cancelled'}))
    with pytest.raises(CRMError, match='changed'):
        edit(b, first, status='in_review')


def test_archived_lead_blocks_task_mutations(b4):
    b = b4
    task = make_task(b)
    b['crm'].archive(b['lead']['id'], Archive(request_key=b['key'](), expected_version=1, archived=True))
    with pytest.raises(CRMError, match='Restore'):
        make_task(b)
    with pytest.raises(CRMError, match='Restore'):
        edit(b, task, status='in_progress')


def test_optout_and_handoff_do_not_block_internal_tasks(b4):
    b = b4
    opted = journey_update(b['crm'], b['lead']['id'], JourneyUpdate(request_key=b['key'](), expected_version=1,
        journey_stage='qualifying', contact_permission='opted_out', handoff_state='requested', reason='Fictional B4 guard check'))['record']
    assert (opted['contact_permission'], opted['handoff_state']) == ('opted_out', 'requested')
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    assert task['status'] == 'in_progress'
    lead = b['crm'].detail('leads', b['lead']['id'])['record']
    assert (lead['contact_permission'], lead['handoff_state'], lead['journey_stage']) == ('opted_out', 'requested', 'qualifying')


def test_owner_http_task_flow(b4):
    b = b4
    with client(b) as api:
        task = api.post('/owner/crm/followup-tasks', json=dict(request_key=b['key'](), lead_id=b['lead']['id'],
            title='Call buyer')).json()['record']
        assert task['status'] == 'open'
        assert api.get('/owner/crm/followup-tasks?lead_id=' + b['lead']['id']).json()['total'] == 1
        assert api.get(f"/owner/crm/followup-tasks/{task['id']}").json()['record']['title'] == 'Call buyer'
        assert api.get(f'/owner/crm/followup-tasks/{uuid4()}').status_code == 404
        advanced = api.post(f"/owner/crm/followup-tasks/{task['id']}/update", json=dict(
            request_key=b['key'](), expected_version=1, status='in_progress', reason='Fictional start')).json()['record']
        assert (advanced['status'], advanced['version']) == ('in_progress', 2)
        assert api.post(f"/owner/crm/followup-tasks/{task['id']}/update", json=dict(
            request_key=b['key'](), expected_version=2, status='in_review', reason='Fictional review')).json()['record']['status'] == 'in_review'
        assert api.post(f"/owner/crm/followup-tasks/{task['id']}/update", json=dict(
            request_key=b['key'](), expected_version=3, status='done', reason='Fictional skip')).status_code == 422
        done = api.post(f"/owner/crm/followup-tasks/{task['id']}/update", json=dict(
            request_key=b['key'](), expected_version=3, status='done', outcome='Fictional visit set.',
            reason='Fictional complete')).json()['record']
        assert (done['status'], done['outcome']) == ('done', 'Fictional visit set.')


def test_history_snapshots_record_full_task_content(b4):
    b = b4
    keys = ['id', 'lead_id', 'property_id', 'title', 'notes', 'due_at', 'assignee', 'status', 'outcome', 'version']
    task = make_task(b, title='Call buyer', notes='Bring rate sheet.', due_at=FUTURE, assignee='Fictional aide',
        property_id=b['prop']['id'])
    record = detail(b['crm'], task['id'])['record']
    opened = detail(b['crm'], task['id'])['history'][0]
    assert opened['before_state'] is None
    assert opened['after_state']['task'] == {k: record[k] for k in keys}
    task = edit(b, task, title='Call buyer about rates', notes='', due_at=None)
    revised = detail(b['crm'], task['id'])['history'][0]
    assert revised['before_state']['task'] == opened['after_state']['task']
    assert (revised['after_state']['task']['title'], revised['after_state']['task']['notes'],
            revised['after_state']['task']['due_at'], revised['after_state']['task']['version']) == (
            'Call buyer about rates', '', None, 2)
    assert revised['after_state']['task']['property_id'] == b['prop']['id']


def test_revision_under_review_shows_changed_content_and_status(b4):
    b = b4
    task = make_task(b)
    task = edit(b, task, status='in_progress')
    task = edit(b, task, status='in_review')
    task = edit(b, task, title='Call buyer about revised rates')
    entry = detail(b['crm'], task['id'])['history'][0]
    assert (entry['before_state']['task']['status'], entry['before_state']['task']['title']) == ('in_review', 'Call buyer about Whitefield')
    assert (entry['after_state']['task']['status'], entry['after_state']['task']['title']) == ('in_progress', 'Call buyer about revised rates')


def test_replay_adds_no_duplicate_activity(b4):
    b = b4
    req = TaskCreate(request_key=b['key'](), lead_id=b['lead']['id'], title='Call buyer')
    task = create_task(b['crm'], req)['record']
    create_task(b['crm'], req)
    assert len(detail(b['crm'], task['id'])['history']) == 1
    req = TaskUpdate(request_key=b['key'](), expected_version=1, status='in_progress', reason='Fictional start')
    update_task(b['crm'], task['id'], req)
    update_task(b['crm'], task['id'], req)
    assert len(detail(b['crm'], task['id'])['history']) == 2


def test_activity_write_failure_rolls_back_task_and_operation(b4, monkeypatch):
    import app.followups as followups_mod
    b = b4
    task = make_task(b)
    def fail(*args, **kwargs):
        raise RuntimeError('Simulated activity outage')
    monkeypatch.setattr(followups_mod, 'log_activity', fail)
    key = b['key']()
    with pytest.raises(RuntimeError, match='Simulated activity outage'):
        update_task(b['crm'], task['id'], TaskUpdate(request_key=key, expected_version=1,
            status='in_progress', reason='Fictional start'))
    assert detail(b['crm'], task['id'])['record']['version'] == 1
    with pytest.raises(CRMError) as exc:
        b['crm'].operation(key)
    assert exc.value.status == 404


def test_detail_returns_only_the_selected_tasks_history(b4):
    b = b4
    first = make_task(b, title='First task')
    second = make_task(b, title='Second task')
    second = edit(b, second, status='in_progress')
    assert second['status'] == 'in_progress'
    history = detail(b['crm'], first['id'])['history']
    assert len(history) == 1
    assert all(entry['after_state']['task']['id'] == first['id'] for entry in history)
    assert len(detail(b['crm'], second['id'])['history']) == 2


def test_legacy_history_snapshots_remain_readable(b4):
    from psycopg.types.json import Jsonb
    b = b4
    task = make_task(b)
    legacy_key = b['key']()
    with b['crm'].connect() as conn:
        conn.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) VALUES (%s,'legacy','legacy','owner',%s,%s)",
            (legacy_key, task['id'], Jsonb({'record': {'id': task['id']}})))
        conn.execute("""INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key)
            VALUES (%s,'owner','Legacy entry',%s,%s,%s)""",
            (b['lead']['id'], Jsonb(None), Jsonb({'task': {'id': task['id'], 'status': 'open', 'outcome': None}}), legacy_key))
    history = detail(b['crm'], task['id'])['history']
    assert len(history) == 2
    legacy = next(entry for entry in history if entry['reason'] == 'Legacy entry')
    assert legacy['after_state']['task'] == {'id': task['id'], 'status': 'open', 'outcome': None}


def committed_lead(b, with_property=False):
    name = 'B4-fictional-' + str(uuid4())
    lead_key = str(uuid4())
    with connect(Settings()) as conn2:
        @contextmanager
        def factory2():
            with conn2.transaction(): yield conn2
        crm2 = CRM(factory2)
        lead = crm2.save('leads', LeadCreate(request_key=lead_key, name=name, email=name + '@example.invalid'))['record']
        prop = None; keys = [lead_key]
        if with_property:
            prop_key = str(uuid4())
            prop = crm2.save('properties', PropertyCreate(request_key=prop_key, title=name, locality=name, price=150, bedrooms=2))['record']
            keys.append(prop_key)
    entry = [lead['id'], prop['id'] if prop else None, keys]
    b['committed'].append(entry)
    return lead, prop


def test_create_serializes_with_concurrent_lead_archive(b4):
    b = b4
    lead, _ = committed_lead(b)
    held = threading.Event(); release = threading.Event(); outcome = {}
    key = b['key']()
    def archive():
        with connect(Settings()) as conn2:
            with conn2.transaction():
                conn2.execute('SELECT id FROM soloestate.leads WHERE id=%s FOR UPDATE', (lead['id'],))
                held.set()
                assert release.wait(30)
                conn2.execute('UPDATE soloestate.leads SET archived_at=clock_timestamp() WHERE id=%s', (lead['id'],))
        outcome['archived'] = True
    def run():
        try:
            with connect(Settings()) as conn3:
                @contextmanager
                def factory3():
                    with conn3.transaction(): yield conn3
                outcome['task'] = create_task(CRM(factory3), TaskCreate(request_key=key, lead_id=lead['id'],
                    title='Raced task'))['record']
        except Exception as exc:
            outcome['error'] = exc
    archiver = threading.Thread(target=archive, daemon=True)
    creator = threading.Thread(target=run, daemon=True)
    archiver.start()
    assert held.wait(30)
    creator.start()
    time.sleep(1)
    assert creator.is_alive() and 'error' not in outcome and 'task' not in outcome
    release.set()
    creator.join(60); archiver.join(60)
    assert not creator.is_alive() and not archiver.is_alive()
    assert outcome.get('archived') is True
    assert isinstance(outcome.get('error'), CRMError) and outcome['error'].status == 422
    assert 'Restore' in str(outcome['error'])
    with connect(Settings()) as probe:
        assert probe.execute('SELECT count(*) FROM soloestate.followup_tasks WHERE lead_id=%s', (lead['id'],)).fetchone()[0] == 0
        assert probe.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s', (key,)).fetchone()[0] == 0


def test_create_serializes_with_concurrent_property_deactivation(b4):
    b = b4
    lead, prop = committed_lead(b, with_property=True)
    held = threading.Event(); release = threading.Event(); outcome = {}
    key = b['key']()
    def deactivate():
        with connect(Settings()) as conn2:
            with conn2.transaction():
                conn2.execute('SELECT id FROM soloestate.properties WHERE id=%s FOR UPDATE', (prop['id'],))
                held.set()
                assert release.wait(30)
                conn2.execute('UPDATE soloestate.properties SET active=false WHERE id=%s', (prop['id'],))
        outcome['deactivated'] = True
    def run():
        try:
            with connect(Settings()) as conn3:
                @contextmanager
                def factory3():
                    with conn3.transaction(): yield conn3
                outcome['task'] = create_task(CRM(factory3), TaskCreate(request_key=key, lead_id=lead['id'],
                    property_id=prop['id'], title='Raced task'))['record']
        except Exception as exc:
            outcome['error'] = exc
    holder = threading.Thread(target=deactivate, daemon=True)
    creator = threading.Thread(target=run, daemon=True)
    holder.start()
    assert held.wait(30)
    creator.start()
    time.sleep(1)
    assert creator.is_alive() and 'error' not in outcome and 'task' not in outcome
    release.set()
    creator.join(60); holder.join(60)
    assert not creator.is_alive() and not holder.is_alive()
    assert outcome.get('deactivated') is True
    assert isinstance(outcome.get('error'), CRMError) and outcome['error'].status == 422
    assert 'active property' in str(outcome['error'])
    with connect(Settings()) as probe:
        assert probe.execute('SELECT count(*) FROM soloestate.followup_tasks WHERE lead_id=%s', (lead['id'],)).fetchone()[0] == 0
        assert probe.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s', (key,)).fetchone()[0] == 0
