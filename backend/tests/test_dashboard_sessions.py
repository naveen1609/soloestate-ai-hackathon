from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.database import connect
from app.dashboard_sessions import DashboardSessions, token_hash
from app.main import create_app
from app import dashboard_api, owner_api


def test_session_api_does_not_accept_invalid_tickets(monkeypatch):
    class Service:
        def exchange(self,ticket):
            raise PermissionError("private detail")
    monkeypatch.setattr(dashboard_api,"DashboardSessions",Service)
    with TestClient(create_app()) as client:
        assert client.post('/owner/dashboard/tickets').status_code==401
        response=client.post('/dashboard/session',json={"ticket":"login_"+"x"*40})
        assert response.status_code==401
        assert "private detail" not in response.text
        assert client.post('/dashboard/session',json={"ticket":"short"}).status_code==422


def test_owner_routes_accept_only_verified_browser_sessions(monkeypatch):
    class Sessions:
        def verify(self,token):
            if token!="sess_valid_test":
                raise PermissionError()
            return {"authenticated":True}
    class Owner:
        def review(self,approval):
            return {"id":str(approval),"status":"pending"}
    monkeypatch.setattr(owner_api,"DashboardSessions",Sessions)
    app=create_app();app.dependency_overrides[owner_api.owner_service]=lambda:Owner()
    path='/owner/approvals/50000000-0000-4000-8000-000000000001'
    with TestClient(app) as client:
        assert client.get(path,headers={"Authorization":"Bearer sess_invalid"}).status_code==401
        assert client.get(path,headers={"Authorization":"Bearer sess_valid_test"}).status_code==200


@pytest.fixture
def sessions(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests for rollback-only dashboard session checks')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            yield DashboardSessions(factory),conn


@pytest.mark.integrity_database
def test_ticket_is_hashed_single_use_and_session_revocable(sessions):
    service,conn=sessions
    ticket=service.issue()['ticket']
    stored=conn.execute('SELECT token_hash FROM soloestate.dashboard_sessions WHERE token_hash=%s',(token_hash(ticket),)).fetchone()[0]
    assert stored!=ticket and len(stored)==64
    result=service.exchange(ticket)
    assert service.verify(result['session_token'])['authenticated'] is True
    with pytest.raises(PermissionError):
        service.exchange(ticket)
    service.revoke(result['session_token'])
    with pytest.raises(PermissionError):
        service.verify(result['session_token'])


@pytest.mark.integrity_database
def test_expired_ticket_and_session_are_rejected(sessions):
    service,conn=sessions
    ticket=service.issue()['ticket']
    conn.execute("UPDATE soloestate.dashboard_sessions SET created_at=clock_timestamp()-interval '3 minutes',expires_at=clock_timestamp()-interval '1 second' WHERE token_hash=%s",(token_hash(ticket),))
    with pytest.raises(PermissionError):
        service.exchange(ticket)
    session=service.exchange(service.issue()['ticket'])['session_token']
    conn.execute("UPDATE soloestate.dashboard_sessions SET created_at=clock_timestamp()-interval '9 hours',expires_at=clock_timestamp()-interval '1 second' WHERE token_hash=%s",(token_hash(session),))
    with pytest.raises(PermissionError):
        service.verify(session)


@pytest.mark.integrity_database
def test_independent_sessions_cannot_exchange_one_ticket_twice(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    service=DashboardSessions();ticket=service.issue()['ticket'];hashes=[token_hash(ticket)]
    gate=Barrier(2)
    def exchange():
        gate.wait(timeout=10)
        try:
            return DashboardSessions().exchange(ticket)
        except PermissionError:
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:exchange(),range(2)))
        for result in results:
            if result:
                hashes.append(token_hash(result['session_token']))
        assert sum(result is not None for result in results)==1
    finally:
        with connect(Settings()) as conn:
            conn.execute('DELETE FROM soloestate.dashboard_sessions WHERE token_hash=ANY(%s::text[])',(hashes,))
