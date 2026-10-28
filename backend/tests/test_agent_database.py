import asyncio
from contextlib import contextmanager
import json
from uuid import uuid4

import psycopg
import pytest

from app.agent.orchestrator import AgentRunner
from app.agent.schemas import RunRequest
from app.agent.store import RunConflict, RunStore
from app.config import Settings
from app.database import connect
from app.services.meta_model import AgentTurn, FunctionCall
from app.tools.service import BusinessTools

pytestmark = pytest.mark.agent_database


def message(value):
    text = json.dumps(value)
    return AgentTurn(response_id=f"resp_{uuid4().hex}", model="mocked-provider", text=text, calls=[], replay=[{"role": "assistant", "content": text}])


def plan(*names):
    return message({"summary": "Rollback-only integration plan", "owner_approval_required": True,
                    "steps": [{"tool": name, "purpose": "Test persisted execution", "required": True} for name in names]})


def call(name, args):
    item = FunctionCall(call_id=f"call_{uuid4().hex}", name=name, arguments=json.dumps(args))
    return AgentTurn(response_id=f"resp_{uuid4().hex}", model="mocked-provider", text="", calls=[item], replay=[{"type": "function_call", **item.model_dump()}])


def done(*sequences):
    return message({"action": "finish", "disposition": "complete", "step_sequences": list(sequences)})


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def respond(self, history, **kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        return response(history) if callable(response) else response


@pytest.fixture
def db_agent(request):
    if not request.config.getoption("--run-agent-db-tests"):
        pytest.skip("Use --run-agent-db-tests for rollback-only agent persistence tests")
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            lead, prop = uuid4(), uuid4()
            locality = f"Fictional Agent Test {uuid4()}"
            conn.execute("""INSERT INTO soloestate.leads (id,name,email,preferred_localities,budget_min,budget_max,bedrooms,created_at)
                VALUES (%s,'Fictional Agent Test',%s,%s,100,200,2,'2034-01-02T00:00:00Z')""", (lead, f"{uuid4()}@example.invalid", [locality]))
            conn.execute("INSERT INTO soloestate.properties (id,title,locality,price,bedrooms) VALUES (%s,'Fictional Agent Test',%s,150,2)", (prop, locality))

            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn

            store, tools = RunStore(factory), BusinessTools(factory)
            def run(model, request=None):
                runner = AgentRunner(model, settings=Settings(_env_file=None), store=store, tools=tools)
                return asyncio.run(runner.run(request or RunRequest(goal="Fictional agent rollback test", request_key=str(uuid4()))))
            yield {"conn": conn, "store": store, "tools": tools, "lead": str(lead), "run": run}


def test_persisted_plan_steps_and_replay_without_new_model_call(db_agent):
    b = db_agent
    request = RunRequest(goal="Fictional agent rollback test", request_key=str(uuid4()), mode="read_only")
    model = Model(plan("get_property_matches"), call("get_property_matches", {"lead_id": b["lead"]}), done(1))
    report = b["run"](model, request)
    assert report["status"] == "completed"
    assert report["verified_steps"][0]["result"]["data"]["matches"][0]["score"] == 100
    assert b["store"].status(report["run_id"])["plan"]["proposal"]["steps"][0]["tool"] == "get_property_matches"
    unused = Model()
    assert b["run"](unused, request) == report
    assert unused.calls == 0


def test_pending_approval_stops_with_actual_draft_id(db_agent):
    b = db_agent
    def use_actual_result(history):
        result = json.loads(history[-1]["output"])
        draft_id = result["data"]["draft"]["id"]
        return call("request_approval", {"kind": "followup", "draft_id": draft_id, "idempotency_key": "approval"})
    model = Model(plan("create_followup_draft", "request_approval"),
                  call("create_followup_draft", {"lead_id": b["lead"], "subject": "Fictional", "body": "Draft only", "idempotency_key": "draft"}), use_actual_result)
    report = b["run"](model)
    assert report["status"] == "awaiting_approval"
    assert report["draft_counts"] == {"draft": 1}
    assert report["approval_counts"] == {"pending": 1}
    assert report["booking_counts"] == {}
    assert report["messages_sent_by_app"] == 0
    assert [row["status"] for row in report["verified_steps"]] == ["succeeded", "awaiting_approval"]


def test_audit_failure_rolls_back_business_write(db_agent, monkeypatch):
    b = db_agent
    original = b["store"].complete_step
    def fail_success(conn, step_id, result):
        if result.status != "error":
            raise psycopg.ProgrammingError("Simulated evidence write failure")
        return original(conn, step_id, result)
    monkeypatch.setattr(b["store"], "complete_step", fail_success)
    model = Model(plan("create_followup_draft"), call("create_followup_draft", {
        "lead_id": b["lead"], "subject": "Fictional", "body": "Must roll back", "idempotency_key": "draft"}))
    report = b["run"](model)
    assert report["status"] == "failed"
    assert report["reason"] == "database_error"
    assert report["draft_counts"] == {}
    assert report["verified_steps"][0]["status"] == "failed"


def test_real_tool_failure_feedback_then_recovery(db_agent):
    b = db_agent
    def recover(history):
        assert json.loads(history[-1]["output"])["code"] == "not_found"
        return call("get_property_matches", {"lead_id": b["lead"]})
    model = Model(plan("get_property_matches"), call("get_property_matches", {"lead_id": str(uuid4())}), recover, done(2))
    report = b["run"](model)
    assert report["status"] == "completed"
    assert [row["status"] for row in report["verified_steps"]] == ["failed", "succeeded"]


def test_running_request_key_does_not_launch_second_worker(db_agent):
    b = db_agent
    request = RunRequest(goal="Fictional agent rollback test", request_key=str(uuid4()))
    b["store"].create(request)
    model = Model()
    with pytest.raises(RunConflict, match="already in progress"):
        b["run"](model, request)
    assert model.calls == 0


@pytest.mark.parametrize("bad", [
    "not JSON",
    '{"summary":"Forged","summary":"Duplicate field"}',
    {"summary": "Unregistered operation", "owner_approval_required": True,
     "steps": [{"tool": "execute_sql", "purpose": "Forbidden", "required": True}]},
])
def test_malformed_model_plan_persists_failure_without_business_writes(db_agent, bad):
    def malformed():
        return message(bad) if isinstance(bad, dict) else message({}).model_copy(
            update={"text": bad, "replay": [{"role": "assistant", "content": bad}]})
    model = Model(malformed(), malformed())
    report = db_agent["run"](model)
    assert report["status"] == "failed"
    assert report["reason"] == "invalid_plan"
    assert report["verified_steps"] == []
    assert report["draft_counts"] == report["booking_counts"] == {}
    assert report["messages_sent_by_app"] == 0
    assert db_agent["store"].status(report["run_id"])["outcome"] == report


@pytest.mark.parametrize("completion", [
    {"action": "finish", "disposition": "complete", "step_sequences": [999]},
    {"action": "finish", "disposition": "complete", "step_sequences": [1],
     "messages_sent": 99, "summary": "All customers contacted"},
])
def test_false_completion_cannot_replace_persisted_tool_evidence(db_agent, completion):
    b = db_agent
    model = Model(plan("get_property_matches"),
                  call("get_property_matches", {"lead_id": b["lead"]}),
                  message(completion), message(completion), message(completion))
    report = b["run"](model)
    assert report["status"] == "failed" and report["reason"] == "failure_limit"
    assert len(report["verified_steps"]) == 1
    assert report["verified_steps"][0]["status"] == "succeeded"
    assert report["messages_sent_by_app"] == 0
    assert report["draft_counts"] == report["booking_counts"] == {}
    assert "All customers contacted" not in json.dumps(report)
    assert b["store"].status(report["run_id"])["status"] == "failed"


def test_distinct_model_call_ids_replay_one_business_action(db_agent):
    b = db_agent
    args = dict(lead_id=b["lead"], subject="Fictional retry", body="Never deliver", idempotency_key="stable-draft")
    model = Model(plan("create_followup_draft"), call("create_followup_draft", args),
                  call("create_followup_draft", args), done(1, 2))
    report = b["run"](model)
    assert report["status"] == "completed"
    assert report["draft_counts"] == {"draft": 1}
    first, replay = [step["result"]["data"] for step in report["verified_steps"]]
    assert first["draft"]["id"] == replay["draft"]["id"]
    assert first["created"] is True and replay["created"] is False
    assert report["messages_sent_by_app"] == 0


@pytest.mark.parametrize("changed", [{"goal": "A different goal"}, {"mode": "draft_first"}])
def test_completed_request_key_cannot_change_goal_or_permissions(db_agent, changed):
    b = db_agent
    request = RunRequest(goal="Fictional read only", request_key=str(uuid4()), mode="read_only")
    report = b["run"](Model(plan("get_property_matches"), call("get_property_matches", {"lead_id": b["lead"]}), done(1)), request)
    unused = Model()
    with pytest.raises(RunConflict, match="different goal or permission"):
        b["run"](unused, request.model_copy(update=changed))
    assert unused.calls == 0
    assert b["store"].status(report["run_id"])["outcome"] == report
