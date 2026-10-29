"""Database-free B5 visit-outcome checks: derivation, graph, schemas, authorization."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import crm_schemas as s
from app.crm_api import service
from app.main import create_app
from app.owner_api import require_owner
from app.visits import FOLLOWUP, OUTCOMES, visit_result


@pytest.mark.parametrize('status,outcome,replaced,expected', [
    ('confirmed', 'completed', False, 'completed'),
    ('confirmed', 'completed', True, 'completed'),
    ('confirmed', 'no_show', False, 'no_show'),
    ('confirmed', 'no_show', True, 'no_show'),
    ('cancelled', None, True, 'rescheduled'),
    ('cancelled', None, False, 'cancelled'),
    ('confirmed', None, False, 'scheduled'),
    ('confirmed', None, True, 'scheduled'),
])
def test_visit_result_derives_all_five_states(status, outcome, replaced, expected):
    assert visit_result(status, outcome, replaced) == expected


def test_visit_result_never_infers_attendance_from_time():
    assert visit_result('confirmed', None, False) == 'scheduled'
    assert set(OUTCOMES) == {'completed', 'no_show'}


@pytest.mark.parametrize('state,targets', [
    ('none', {'pending'}),
    ('pending', {'done', 'none'}),
    ('done', {'pending'}),
])
def test_followup_graph_matches_contract(state, targets):
    assert FOLLOWUP[state] == targets


def test_followup_graph_has_no_done_to_none_edge():
    assert 'none' not in FOLLOWUP['done']


def test_outcome_input_accepts_partial_updates():
    full = s.OutcomeUpdate(request_key='key', expected_version=1, visit_outcome='completed',
        outcome_note='Buyer toured twice.', followup_state='pending',
        followup_note='Send inventory sheet.', reason='Fictional visit')
    assert full.visit_outcome == 'completed' and full.followup_state == 'pending'
    assert 'outcome_note' in full.model_fields_set
    note_only = s.OutcomeUpdate(request_key='key', expected_version=2, followup_note='Revised next action.',
        reason='Fictional visit')
    assert note_only.visit_outcome is None and note_only.followup_state is None


@pytest.mark.parametrize('change', [
    {'visit_outcome': 'visited'}, {'visit_outcome': 'cancelled'}, {'followup_state': 'open'},
    {'followup_state': 'archived'}, {'reason': '   '}, {'reason': ''}, {'expected_version': 0},
    {'visit_outcome': 'completed', 'channel': 'sms'}, {'visit_outcome': 'completed', 'sent': True},
])
def test_outcome_input_rejects_invalid_or_extra_fields(change):
    base = dict(request_key='key', expected_version=1, reason='Fictional visit')
    with pytest.raises(ValidationError):
        s.OutcomeUpdate(**{**base, **change})


def test_outcome_route_requires_owner():
    with TestClient(create_app()) as client:
        assert client.post(f'/owner/crm/appointments/{uuid4()}/outcome', json={}).status_code == 401


def test_invalid_outcome_filter_rejected_before_database():
    from contextlib import contextmanager
    from app.crm import CRM
    app = create_app()
    app.dependency_overrides[require_owner] = lambda: 'owner'

    @contextmanager
    def unreachable():
        raise AssertionError('Invalid outcome filter must not reach the database')
        yield None

    app.dependency_overrides[service] = lambda: CRM(unreachable)
    with TestClient(app) as client:
        assert client.get('/owner/crm/appointments?outcome=visited').status_code == 422
