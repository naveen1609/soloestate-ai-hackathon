from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from app.tools import schemas as s
from app.tools.service import BusinessTools, REGISTRY, tool_definitions


def test_exact_allowlist_and_schemas():
    assert set(REGISTRY) == {"get_leads", "get_property_matches", "get_available_slots", "create_followup_draft", "request_approval", "book_site_visit", "get_run_status"}
    for definition in tool_definitions():
        assert definition["parameters"]["additionalProperties"] is False
        assert "run_id" not in definition["parameters"]["properties"]
        assert "status" not in definition["parameters"]["properties"]


@pytest.mark.parametrize("args", [
    {"since": "2026-11-01", "until": "2026-11-08T00:00:00Z"},
    {"since": "2026-11-01T00:00:00", "until": "2026-11-08T00:00:00Z"},
    {"since": "2026-11-08T00:00:00Z", "until": "2026-11-01T00:00:00Z"},
    {"since": "2026-11-01T00:00:00Z", "until": "2027-11-01T00:00:00Z"},
    {"since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z", "limit": 0},
    {"since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z", "limit": "10"},
    {"since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z", "limit": True},
])
def test_invalid_window(args):
    with pytest.raises(ValidationError):
        s.GetLeadsInput.model_validate(args)


@pytest.mark.parametrize("kind,fields", [
    ("followup", {}), ("booking", {"draft_id": str(uuid4())}),
    ("followup", {"draft_id": str(uuid4()), "lead_id": str(uuid4())}),
    ("booking", {"lead_id": str(uuid4()), "slot_id": str(uuid4())}),
])
def test_invalid_approval_targets(kind, fields):
    with pytest.raises(ValidationError):
        s.ApprovalInput(kind=kind, idempotency_key="test", **fields)


@pytest.mark.parametrize("name,args,code", [
    ("execute_sql", {"sql": "DROP TABLE leads"}, "unknown_tool"),
    ("approve_request", {}, "unknown_tool"),
    ("book_site_visit", {"approval_id": str(uuid4()), "idempotency_key": "x", "status": "approved"}, "invalid_arguments"),
    ("get_run_status", {"run_id": str(uuid4())}, "invalid_arguments"),
    ("create_followup_draft", {"lead_id": str(uuid4()), "idempotency_key": "x", "subject": " ", "body": "body"}, "invalid_arguments"),
])
def test_invalid_dispatch_never_connects(name, args, code):
    def forbidden():
        pytest.fail("Invalid tool calls must fail before database access")
    result = BusinessTools(forbidden).execute(name, args, s.ToolContext(run_id=uuid4()))
    assert result.status == "error"
    assert result.code == code


def test_database_failure_is_safe():
    def fail():
        raise psycopg.OperationalError("private database password")
    result = BusinessTools(fail).execute("get_run_status", {}, s.ToolContext(run_id=uuid4()))
    assert result.status == "error"
    assert result.code == "database_error"
    assert "password" not in result.model_dump_json()


def test_commit_failure_cannot_report_success():
    context = MagicMock()
    conn = context.__enter__.return_value
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.return_value.fetchall.return_value = []
    context.__exit__.side_effect = psycopg.OperationalError("commit failed")
    result = BusinessTools(lambda: context).execute("get_leads", {"since": "2026-11-01T00:00:00Z", "until": "2026-11-08T00:00:00Z"}, s.ToolContext(run_id=uuid4()))
    assert result.status == "error"
    assert result.code == "database_error"
    assert result.data is None


@pytest.mark.parametrize("error",[psycopg.errors.LockNotAvailable,psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure])
def test_only_confirmed_rollback_errors_are_retried_once(error):
    attempts=[]
    context=MagicMock()
    context.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.return_value.fetchall.return_value=[]
    def factory():
        attempts.append(1)
        if len(attempts)==1:
            raise error("sensitive driver text")
        return context
    result=BusinessTools(factory).execute("get_leads",{"since":"2026-11-01T00:00:00Z","until":"2026-11-08T00:00:00Z"},s.ToolContext(run_id=uuid4()))
    assert result.status == "succeeded"
    assert len(attempts)==2


def test_transaction_retry_limit_and_no_network_retry():
    attempts=[]
    def locked():
        attempts.append(1)
        raise psycopg.errors.LockNotAvailable("sensitive driver text")
    args={"since":"2026-11-01T00:00:00Z","until":"2026-11-08T00:00:00Z"}
    result=BusinessTools(locked).execute("get_leads",args,s.ToolContext(run_id=uuid4()))
    assert len(attempts)==2 and result.status=="error"
    assert "sensitive" not in result.model_dump_json()
    attempts.clear()
    def disconnected():
        attempts.append(1)
        raise psycopg.OperationalError("sensitive connection text")
    assert BusinessTools(disconnected).execute("get_leads",args,s.ToolContext(run_id=uuid4())).status=="error"
    assert len(attempts)==1
