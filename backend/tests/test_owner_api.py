from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.owner import authenticate_owner, payload_hash, DecisionRequest
from app.owner_api import owner_settings, owner_service

TOKEN = "unit-owner-token-" + "x" * 48


def client(service=None, token=TOKEN):
    app = create_app()
    app.dependency_overrides[owner_settings] = lambda: Settings(_env_file=None, owner_api_token=token)
    if service:
        app.dependency_overrides[owner_service] = lambda: service
    return TestClient(app)


@pytest.mark.parametrize("token", [None,"wrong", ""])
def test_owner_auth_rejects_missing_or_wrong_credentials(token):
    with pytest.raises(PermissionError):
        authenticate_owner(token, Settings(_env_file=None, owner_api_token=TOKEN))


def test_missing_server_credential_fails_closed():
    with client(token=None) as api:
        assert api.get(f"/owner/approvals/{uuid4()}", headers={"Authorization":f"Bearer {TOKEN}"}).status_code == 401


@pytest.mark.parametrize("method,path,body", [
    ("get", "/owner/approvals", None),
    ("get", f"/owner/approvals/{uuid4()}", None),
    ("get", f"/owner/runs/{uuid4()}", None),
    ("post", "/owner/runs", {"goal":"Review fictional leads","request_key":"test"}),
    ("post", f"/owner/runs/{uuid4()}/resume", {"recover":False}),
    ("post", f"/owner/approvals/{uuid4()}/decision", {"decision":"approved","expected_payload_hash":"a"*64,"idempotency_key":"test"}),
])
def test_all_owner_routes_require_auth(method, path, body):
    with client() as api:
        assert api.request(method,path,json=body).status_code == 401
        assert api.get("/health").status_code == 200


def test_authenticated_review_and_safe_database_error():
    class Service:
        def review(self, approval_id):
            return {"id":str(approval_id),"status":"pending","payload_hash":"a"*64}
    with client(Service()) as api:
        assert api.get(f"/owner/approvals/{uuid4()}",headers={"Authorization":f"Bearer {TOKEN}"}).status_code == 200
    class Failed:
        def review(self, approval_id):
            raise psycopg.OperationalError("private-database-password")
    with client(Failed()) as api:
        response=api.get(f"/owner/approvals/{uuid4()}",headers={"Authorization":f"Bearer {TOKEN}"})
        assert response.status_code == 503
        assert response.json()["status"] == "unconfirmed"
        assert "private" not in response.text


def test_payload_hash_is_canonical():
    assert payload_hash({"subject":"Héllo","body":"Draft"}) == payload_hash({"body":"Draft","subject":"Héllo"})
    assert payload_hash({"body":"One"}) != payload_hash({"body":"Two"})


def test_decision_requires_review_hash_and_rejects_extra_authority():
    with pytest.raises(ValidationError):
        DecisionRequest(decision="approved",expected_payload_hash="wrong",idempotency_key="test")
    with pytest.raises(ValidationError):
        DecisionRequest(decision="approved",expected_payload_hash="a"*64,idempotency_key="test",actor="model")
