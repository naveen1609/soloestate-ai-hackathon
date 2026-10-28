import json

import pytest

from app.config import Settings
from scripts import demo_preflight as demo


def settings(**changes):
    return Settings(_env_file=None, model_api_key="private-model-key", model_name="test-model",
                    owner_api_token="a" * 64, **changes)


def fixtures(monkeypatch):
    monkeypatch.setattr(demo, "database_check", lambda _: {"migrations": 3})
    monkeypatch.setattr(demo, "tools_check", lambda _: {"available_slots": 0})


def test_readiness_does_not_claim_model_connectivity_or_bookable_slots(monkeypatch):
    fixtures(monkeypatch)
    monkeypatch.setattr(demo.httpx, "get", lambda *a, **k: pytest.fail("No HTTP unless opted in"))
    result = demo.inspect(settings=settings())
    assert result["status"] == "ok"
    assert result["model_connectivity"] == "not_tested" and result["business_writes"] is False
    assert result["checks"][2]["details"]["available_slots"] == 0
    assert "private-model-key" not in json.dumps(result)


def test_database_failure_is_redacted_and_tools_do_not_run(monkeypatch):
    def fail(_):
        raise RuntimeError("postgresql://private-password@secret-host/db")
    monkeypatch.setattr(demo, "database_check", fail)
    monkeypatch.setattr(demo, "tools_check", lambda _: pytest.fail("Database failed"))
    result = demo.inspect(settings=settings())
    assert result["status"] == "error"
    assert "private-password" not in json.dumps(result)
    assert not any(item["name"] == "read_only_tools" for item in result["checks"])


def test_missing_model_settings_fails_without_claiming_inference(monkeypatch):
    fixtures(monkeypatch)
    configured = settings().model_copy(update={"model_api_key": None})
    result = demo.inspect(settings=configured)
    assert result["status"] == "error" and result["checks"][0]["status"] == "error"


def test_invalid_environment_is_sanitized(monkeypatch):
    def fail():
        raise ValueError("private-env-value")
    monkeypatch.setattr(demo, "Settings", fail)
    result = demo.inspect()
    assert result["status"] == "error" and "private-env-value" not in json.dumps(result)


def test_service_checks_reject_unhealthy_frontend(monkeypatch):
    fixtures(monkeypatch)
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"status": "ok", "backend": {"status": "unavailable"}}
    monkeypatch.setattr(demo.httpx, "get", lambda *a, **k: Response())
    result = demo.inspect(settings=settings(), services=True)
    assert result["status"] == "error"
    assert result["checks"][-1]["name"] == "frontend_health"
    assert result["checks"][-1]["status"] == "error"
