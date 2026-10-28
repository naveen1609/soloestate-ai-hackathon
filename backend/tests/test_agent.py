import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from app.agent.orchestrator import AgentRunner
from app.agent.schemas import RunRequest, json_object
from app.config import Settings
from app.services.meta_model import AgentTurn, FunctionCall, MetaModelService, ModelServiceError
from app.tools.schemas import ToolResult


def plan(tool="get_leads"):
    return {"summary": "Proposed review", "steps": [{"tool": tool, "purpose": "Review real records", "required": True}], "owner_approval_required": True}


def text_turn(value):
    text = value if isinstance(value, str) else json.dumps(value)
    return AgentTurn(response_id=f"resp_{uuid4().hex}", model="test-meta", text=text, calls=[],
                     replay=[{"role": "assistant", "content": text}])


def tool_turn(name="get_leads", arguments=None, call_id=None):
    arguments = arguments if arguments is not None else {"since": "2026-11-02T00:00:00+05:30", "until": "2026-11-09T00:00:00+05:30"}
    call = FunctionCall(call_id=call_id or f"call_{uuid4().hex}", name=name, arguments=arguments if isinstance(arguments, str) else json.dumps(arguments))
    return AgentTurn(response_id=f"resp_{uuid4().hex}", model="test-meta", text="", calls=[call], replay=[{"type": "function_call", **call.model_dump()}])


def done(sequences, disposition="complete"):
    return text_turn({"action": "finish", "disposition": disposition, "step_sequences": sequences})


class Model:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.inputs = []

    async def respond(self, history, **kwargs):
        self.inputs.append(deepcopy(history))
        response = self.turns.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Store:
    def __init__(self):
        self.id = uuid4()
        self.steps = []
        self.saved_plan = None

    def create(self, request):
        return {"id": self.id}, True

    def checkpoint(self, run_id, trace, events, plan=None, **kwargs):
        if plan:
            self.saved_plan = plan

    def start_step(self, run_id, sequence, call_id, name, args):
        step_id = uuid4()
        self.steps.append({"id": step_id, "sequence": sequence, "tool": name, "status": "running", "arguments": args})
        return step_id

    def complete_step(self, conn, step_id, result):
        step = next(step for step in self.steps if step["id"] == step_id)
        step.update(status=result.status, result=result.model_dump(mode="json"))

    def failed_step(self, step_id, result):
        self.complete_step(None, step_id, result)

    def finish(self, run_id, status, reason, trace, events):
        return {"status": status, "reason": reason, "steps": self.steps, "model_trace": trace,
                "events": events, "messages_sent_by_app": 0}


class Tools:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, name, arguments, context, on_result):
        self.calls.append((name, arguments, context.run_id))
        result = self.results.pop(0) if self.results else ToolResult(tool=name, status="succeeded", data={"leads": [], "truncated": False})
        if result.status != "error":
            on_result(None, result)
        return result


def execute(model, tools=None, store=None, **settings):
    store = store or Store()
    tools = tools or Tools()
    runner = AgentRunner(model, tools=tools, store=store, settings=Settings(_env_file=None, **settings))
    return asyncio.run(runner.run(RunRequest(goal="Review fictional leads", request_key="test", mode="read_only"))), store, tools


def test_plan_then_tools_then_verified_finish():
    model = Model(text_turn(plan()), tool_turn(), done([1]))
    report, store, tools = execute(model)
    assert report["status"] == "completed"
    assert store.saved_plan.summary == "Proposed review"
    observed = [item for item in model.inputs[2] if item.get("type") == "function_call_output"]
    actual = json.loads(observed[0]["output"])
    assert actual["step_sequence"] == 1
    assert actual["data"] == {"leads": [], "truncated": False}
    assert tools.calls[0][2] == store.id


def test_malformed_plan_can_be_repaired():
    report, _, _ = execute(Model(text_turn("not JSON"), text_turn(plan()), tool_turn(), done([1])))
    assert report["status"] == "completed"
    assert report["events"][0]["code"] == "invalid_plan"


def test_repeated_bad_plan_stops_without_tool_execution():
    report, _, tools = execute(Model(text_turn(plan("book_site_visit")), text_turn(plan("book_site_visit"))))
    assert report["reason"] == "invalid_plan"
    assert not tools.calls


def test_tool_failure_is_returned_and_model_can_adapt():
    tools = Tools(ToolResult(tool="get_leads", status="error", code="not_found", message="No such filter"),
                  ToolResult(tool="get_leads", status="succeeded", data={"leads": [], "truncated": False}))
    model = Model(text_turn(plan()), tool_turn(), tool_turn(), done([2]))
    report, _, _ = execute(model, tools)
    assert report["status"] == "completed"
    assert report["steps"][0]["status"] == "error"
    observed = [item for item in model.inputs[2] if item.get("type") == "function_call_output"]
    assert json.loads(observed[0]["output"])["code"] == "not_found"


def test_approval_stops_before_any_following_call():
    model = Model(text_turn(plan("request_approval")), tool_turn("request_approval", {"kind": "followup", "draft_id": str(uuid4()), "idempotency_key": "a"}))
    tools = Tools(ToolResult(tool="request_approval", status="approval_required", data={"approval": {"status": "pending"}}))
    runner = AgentRunner(model, tools=tools, store=Store(), settings=Settings(_env_file=None))
    report = asyncio.run(runner.run(RunRequest(goal="Prepare a draft", request_key="approval")))
    assert report["status"] == "awaiting_approval"
    assert len(model.inputs) == 2
    assert report["messages_sent_by_app"] == 0


def test_unknown_tools_are_never_dispatched():
    model = Model(text_turn(plan()), tool_turn("execute_sql", {}), tool_turn(), done([1]))
    report, _, tools = execute(model)
    assert report["status"] == "completed"
    assert [name for name, _, _ in tools.calls] == ["get_leads"]
    assert report["events"][0]["code"] == "unknown_tool"


def test_read_only_permissions_cannot_be_overridden_by_model():
    model = Model(text_turn(plan()), tool_turn("create_followup_draft", {}), tool_turn(), done([1]))
    report, _, tools = execute(model)
    assert report["status"] == "completed"
    assert [name for name, _, _ in tools.calls] == ["get_leads"]
    assert report["events"][0]["code"] == "permission_denied"


@pytest.mark.parametrize("bad", [done([99]), text_turn('I sent every customer a message.'), done([])])
def test_unverified_completion_cannot_be_reported(bad):
    report, _, _ = execute(Model(text_turn(plan()), tool_turn(), bad, done([1])))
    assert report["status"] == "completed"
    assert report["events"][0]["code"] == "unsupported_completion"
    assert "sent every" not in json.dumps(report, default=str)


def test_no_tool_evidence_cannot_complete():
    report, _, _ = execute(Model(text_turn(plan()), done([]), done([], "blocked")))
    assert report["status"] == "failed"
    assert report["reason"] == "model_blocked"


def test_failure_limit_stops_repeated_errors():
    model = Model(text_turn(plan()), tool_turn("bad", {}), tool_turn("bad", {}), tool_turn("bad", {}))
    report, _, _ = execute(model)
    assert report["reason"] == "failure_limit"


def test_model_turn_and_tool_call_limits():
    report, _, _ = execute(Model(text_turn(plan()), tool_turn()), agent_max_turns=2)
    assert report["reason"] == "model_turn_limit"
    report, _, tools = execute(Model(text_turn(plan()), tool_turn(), tool_turn()), agent_max_tool_calls=1)
    assert report["reason"] == "tool_call_limit"
    assert len(tools.calls) == 1


def test_replayed_call_id_stops():
    report, _, tools = execute(Model(text_turn(plan()), tool_turn(call_id="repeat"), tool_turn(call_id="repeat")))
    assert report["reason"] == "duplicate_call_id"
    assert len(tools.calls) == 1


def test_model_failure_is_not_business_success():
    report, _, _ = execute(Model(ModelServiceError("timeout", "Provider timed out")))
    assert report["status"] == "failed"
    assert report["reason"] == "model_timeout"


def test_malformed_tool_arguments_return_feedback_without_dispatch():
    model = Model(text_turn(plan()), tool_turn(arguments='{"a":1,"a":2}'), tool_turn(), done([1]))
    report, _, tools = execute(model)
    assert report["status"] == "completed"
    assert len(tools.calls) == 1
    feedback = json.loads(model.inputs[2][-1]["output"])
    assert feedback["code"] == "invalid_arguments"
    assert feedback["step_sequence"] is None


def test_history_limit_stops_without_truncating_evidence():
    proposal = text_turn(plan())
    proposal.replay = [{"role": "assistant", "content": "x" * 500_001}]
    report, _, tools = execute(Model(proposal))
    assert report["reason"] == "history_limit"
    assert not tools.calls


def test_deadline_before_model_call():
    times = iter([0, 11])
    model = Model()
    runner = AgentRunner(model, store=Store(), tools=Tools(), settings=Settings(_env_file=None, agent_timeout_seconds=10), clock=lambda: next(times))
    report = asyncio.run(runner.run(RunRequest(goal="Review", request_key="deadline")))
    assert report["reason"] == "deadline"
    assert not model.inputs


def test_deadline_interrupts_model_but_not_database_writes():
    class SlowModel:
        async def respond(self, *args, **kwargs):
            await asyncio.sleep(0.05)
    times = iter([0, 9.999])
    runner = AgentRunner(SlowModel(), store=Store(), tools=Tools(), settings=Settings(_env_file=None, agent_timeout_seconds=10), clock=lambda: next(times))
    report = asyncio.run(runner.run(RunRequest(goal="Review", request_key="deadline")))
    assert report["reason"] == "deadline"


def test_database_tool_error_stops_after_recording_failure():
    tools = Tools(ToolResult(tool="get_leads", status="error", code="database_error", message="Unavailable"))
    report, store, _ = execute(Model(text_turn(plan()), tool_turn()), tools)
    assert report["reason"] == "database_error"
    assert store.steps[0]["status"] == "error"


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', '{"a":NaN}', '[]', 'not JSON'])
def test_noncanonical_json_rejected(value):
    with pytest.raises(ValueError):
        json_object(value)


def test_real_sdk_native_function_call_and_reasoning_replay():
    seen = []
    async def exercise():
        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            output = [
                {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque-test-payload"},
                {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "get_run_status", "arguments": "{}"},
            ]
            return httpx.Response(200, json={"id": "resp_test", "model": "test-meta", "status": "completed", "output": output})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaModelService(Settings(_env_file=None, model_api_key="fake-key", model_name="test-meta"), http_client=client) as model:
            first = await model.respond([{"role": "user", "content": "Review"}], instructions="Policy", tools=[])
            second_history = first.replay + [{"type": "function_call_output", "call_id": "call_test", "output": '{"status":"succeeded"}'}]
            await model.respond(second_history, instructions="Policy", tools=[])
            assert first.calls[0].name == "get_run_status"
    asyncio.run(exercise())
    assert seen[0]["store"] is False
    assert seen[0]["include"] == ["reasoning.encrypted_content"]
    assert seen[1]["input"][0]["encrypted_content"] == "opaque-test-payload"
    assert seen[1]["input"][2]["type"] == "function_call_output"
