from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.crm import CRMError
from app.crm_schemas import TaskCreate, TaskUpdate
from app.followups import TRANSITIONS, overdue, resolve_status
from app.main import create_app


def test_task_schemas_accept_valid_requests():
    create = TaskCreate(request_key='t', lead_id=uuid4(), title='Call buyer')
    assert (create.notes, create.due_at, create.assignee, create.property_id) == ('', None, 'owner', None)
    assert TaskUpdate(request_key='t', expected_version=1, status='in_progress', reason='Fictional start')


@pytest.mark.parametrize('schema,body', [
    (TaskCreate, dict(title='  ')),
    (TaskCreate, dict(assignee=' ')),
    (TaskCreate, dict(lead_id='not-a-uuid')),
    (TaskCreate, dict(title='Hi', due_at='not-a-date')),
    (TaskCreate, dict(title='Hi', channel='sms')),
    (TaskUpdate, dict(reason=' ')),
    (TaskUpdate, dict(status='sent', reason='Fictional')),
    (TaskUpdate, dict(status='delivered', reason='Fictional')),
    (TaskUpdate, dict(expected_version=True, reason='Fictional')),
    (TaskUpdate, dict(reason='Fictional', actor='model')),
])
def test_task_schemas_reject_invalid_requests(schema, body):
    base = dict(request_key='t', expected_version=1, lead_id=uuid4(), title='Call buyer', reason='Fictional')
    valid = {k: v for k, v in base.items() if k in schema.model_fields}
    with pytest.raises(ValidationError):
        schema(**{**valid, **body})


def test_transition_graph_pins_lifecycle_without_delivery():
    assert TRANSITIONS == {'open': {'in_progress', 'cancelled'}, 'in_progress': {'open', 'in_review', 'cancelled'},
                           'in_review': {'in_progress', 'done', 'cancelled'}, 'done': set(), 'cancelled': {'open'}}
    assert 'sent' not in TRANSITIONS and 'delivered' not in TRANSITIONS
    assert all('sent' not in targets and 'delivered' not in targets for targets in TRANSITIONS.values())


@pytest.mark.parametrize('current,content,target,expected', [
    ('open', False, None, 'open'),
    ('open', False, 'in_progress', 'in_progress'),
    ('open', True, None, 'open'),
    ('in_progress', False, 'in_review', 'in_review'),
    ('in_review', True, None, 'in_progress'),
    ('in_review', False, 'done', 'done'),
    ('in_review', True, 'in_progress', 'in_progress'),
    ('in_review', True, 'cancelled', 'cancelled'),
    ('in_review', True, 'open', 'open'),
    ('cancelled', False, 'open', 'open'),
    ('done', False, None, 'done'),
])
def test_resolve_status_matrix(current, content, target, expected):
    assert resolve_status(current, content, target) == expected


@pytest.mark.parametrize('current,content,target', [
    ('open', False, 'done'),
    ('open', False, 'in_review'),
    ('in_progress', False, 'done'),
    ('in_review', True, 'done'),
    ('in_review', True, 'in_review'),
    ('in_review', False, 'open'),
    ('done', False, 'open'),
    ('cancelled', False, 'done'),
])
def test_resolve_status_rejects_invalid_transitions(current, content, target):
    with pytest.raises(CRMError):
        resolve_status(current, content, target)


def test_overdue_derives_from_due_date_and_active_status():
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    past = now - timedelta(hours=1)
    future = now + timedelta(hours=1)
    assert overdue(past, 'open', now) and overdue(past, 'in_review', now)
    assert not overdue(future, 'open', now)
    assert not overdue(None, 'open', now)
    assert not overdue(past, 'done', now) and not overdue(past, 'cancelled', now)


@pytest.mark.parametrize('suffix,method', [('followup-tasks', 'POST'), ('followup-tasks', 'GET')])
def test_task_routes_require_owner(suffix, method):
    with TestClient(create_app()) as client:
        assert client.request(method, f'/owner/crm/{suffix}', json={} if method == 'POST' else None).status_code == 401
