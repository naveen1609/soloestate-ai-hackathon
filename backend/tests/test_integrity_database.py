"""Real DB integrity tests. Rollback fixtures plus explicitly cleaned concurrent fixtures."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from app.agent.orchestrator import AgentRunner
from app.agent.schemas import Plan, RunRequest
from app.agent.store import RunStore, RunConflict
from app.config import Settings
from app.database import connect
from app.owner import OwnerService, DecisionRequest
from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools
from test_agent_database import Model, plan, call, done

pytestmark = pytest.mark.integrity_database


def make_records(conn):
    run, other, lead, prop, slot = [uuid4() for _ in range(5)]
    locality = f"Phase7 Fictional {uuid4()}"
    start = conn.execute("SELECT greatest(coalesce(max(ends_at),CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)+interval '730 days' FROM soloestate.visit_slots").fetchone()[0]
    for run_id in (run, other):
        conn.execute("INSERT INTO soloestate.runs (id,request_key,goal,status,plan) VALUES (%s,%s,'Phase7 isolated integrity fixture','pending','{\"policy\":{\"mode\":\"draft_first\"}}')", (run_id,str(uuid4())))
    conn.execute("""INSERT INTO soloestate.leads (id,name,email,preferred_localities,budget_min,budget_max,bedrooms)
        VALUES (%s,'Phase7 isolated fictional lead',%s,%s,100,200,2)""", (lead,f"{uuid4()}@example.invalid",[locality]))
    conn.execute("INSERT INTO soloestate.properties (id,title,locality,price,bedrooms) VALUES (%s,'Phase7 Fictional Property',%s,150,2)",(prop,locality))
    conn.execute("INSERT INTO soloestate.visit_slots (id,starts_at,ends_at) VALUES (%s,%s,%s)",(slot,start,start+timedelta(minutes=30)))
    return {"run":run,"other":other,"lead":lead,"property":prop,"slot":slot}


@pytest.fixture
def integrity(request):
    if not request.config.getoption("--run-integrity-db-tests"):
        pytest.skip("Use --run-integrity-db-tests")
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            data = make_records(conn)
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            data.update(conn=conn,factory=factory,tools=BusinessTools(factory),owner=OwnerService(factory),store=RunStore(factory))
            yield data


@pytest.fixture
def concurrent_fixture(request):
    if not request.config.getoption("--run-integrity-db-tests"):
        pytest.skip("Use --run-integrity-db-tests")
    with connect(Settings()) as conn:
        data = make_records(conn)
    data.update(tools=BusinessTools(),owner=OwnerService(),store=RunStore())
    try:
        yield data
    finally:
        # Delete only fixture-owned UUIDs, in FK order; no application seed/run is touched.
        with connect(Settings()) as conn:
            runs = [data["run"], data["other"]]
            conn.execute("DELETE FROM soloestate.bookings WHERE run_id=ANY(%s::uuid[])",(runs,))
            conn.execute("DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[]))",(runs,))
            conn.execute("DELETE FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[])",(runs,))
            conn.execute("DELETE FROM soloestate.followup_drafts WHERE run_id=ANY(%s::uuid[])",(runs,))
            conn.execute("DELETE FROM soloestate.run_steps WHERE run_id=ANY(%s::uuid[])",(runs,))
            conn.execute("DELETE FROM soloestate.runs WHERE id=ANY(%s::uuid[])",(runs,))
            conn.execute("DELETE FROM soloestate.visit_slots WHERE id=%s",(data["slot"],))
            conn.execute("DELETE FROM soloestate.properties WHERE id=%s",(data["property"],))
            conn.execute("DELETE FROM soloestate.leads WHERE id=%s",(data["lead"],))


def draft_approval(b):
    ctx=ToolContext(run_id=b["run"])
    draft=b["tools"].execute("create_followup_draft",{"lead_id":str(b["lead"]),"subject":"Fictional review","body":"Never send this fixture","idempotency_key":"draft"},ctx)
    result=b["tools"].execute("request_approval",{"kind":"followup","draft_id":draft.data["draft"]["id"],"idempotency_key":"approval"},ctx)
    assert result.status == "approval_required", result
    return result.data["approval"]["id"], draft.data["draft"]["id"]


def test_rejected_run_resume_is_idempotent_and_never_calls_model(integrity):
    b = integrity
    approval, draft = draft_approval(b)
    review = b["owner"].review(approval)
    decision = b["owner"].decide(approval, DecisionRequest(decision="rejected",
        expected_payload_hash=review["payload_hash"], idempotency_key=str(uuid4())))
    model = Model()
    runner = AgentRunner(model, settings=Settings(_env_file=None), store=b["store"], tools=b["tools"])
    for recover in (False, True):
        report = asyncio.run(runner.resume(b["run"], recover=recover))
        assert report == decision["run"]
        assert report["status"] == "cancelled"
        assert report["draft_counts"] == {"rejected": 1}
        assert report["booking_counts"] == {} and report["messages_sent_by_app"] == 0
    assert model.calls == 0
    assert b["conn"].execute("SELECT count(*) FROM soloestate.approval_decisions WHERE approval_id=%s", (approval,)).fetchone()[0] == 1


def test_property_withdrawal_after_consent_leaves_booking_uncommitted(integrity):
    b = integrity
    ctx = ToolContext(run_id=b["run"])
    result = b["tools"].execute("request_approval", dict(kind="booking", lead_id=str(b["lead"]),
        property_id=str(b["property"]), slot_id=str(b["slot"]), idempotency_key="review"), ctx)
    approval = result.data["approval"]["id"]
    review = b["owner"].review(approval)
    b["owner"].decide(approval, DecisionRequest(decision="approved",
        expected_payload_hash=review["payload_hash"], idempotency_key=str(uuid4())))
    b["conn"].execute("UPDATE soloestate.properties SET active=false WHERE id=%s", (b["property"],))
    result = b["tools"].execute("book_site_visit", dict(approval_id=approval,idempotency_key="book"),ctx)
    assert result.status == "error" and result.code == "property_unavailable"
    assert b["conn"].execute("SELECT count(*) FROM soloestate.bookings WHERE run_id=%s",(b["run"],)).fetchone()[0] == 0
    assert b["conn"].execute("SELECT status FROM soloestate.approvals WHERE id=%s",(approval,)).fetchone()[0] == "approved"


def test_owner_audit_failure_rolls_back_consent_and_draft_status(integrity, monkeypatch):
    b = integrity
    approval, draft = draft_approval(b)
    review = b["owner"].review(approval)
    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("Simulated outcome storage outage")
    monkeypatch.setattr(RunStore, "finish", unavailable)
    with pytest.raises(psycopg.OperationalError):
        b["owner"].decide(approval, DecisionRequest(decision="approved",
            expected_payload_hash=review["payload_hash"], idempotency_key=str(uuid4())))
    assert b["conn"].execute("SELECT status FROM soloestate.approvals WHERE id=%s",(approval,)).fetchone()[0] == "pending"
    assert b["conn"].execute("SELECT status FROM soloestate.followup_drafts WHERE id=%s",(draft,)).fetchone()[0] == "draft"
    assert b["conn"].execute("SELECT count(*) FROM soloestate.approval_decisions WHERE approval_id=%s",(approval,)).fetchone()[0] == 0


def booking_approval(b, run=None):
    result=b["tools"].execute("request_approval",{"kind":"booking","lead_id":str(b["lead"]),"property_id":str(b["property"]),"slot_id":str(b["slot"]),"idempotency_key":str(uuid4())},ToolContext(run_id=run or b["run"]))
    assert result.status == "approval_required", result
    return result.data["approval"]["id"]


def decision(b, approval, value="approved", key=None):
    return DecisionRequest(decision=value,expected_payload_hash=b["owner"].review(approval)["payload_hash"],idempotency_key=key or str(uuid4()))


def test_review_decision_and_idempotent_replay(integrity):
    b=integrity
    approval,draft=draft_approval(b)
    request=decision(b,approval)
    first=b["owner"].decide(approval,request)
    assert first["run"]["status"] == "pending"
    assert b["owner"].decide(approval,request)["replayed"] is True
    assert b["conn"].execute("SELECT status FROM soloestate.followup_drafts WHERE id=%s",(draft,)).fetchone()[0] == "approved"
    assert b["conn"].execute("SELECT count(*) FROM soloestate.approval_decisions WHERE approval_id=%s",(approval,)).fetchone()[0] == 1
    with pytest.raises(RunConflict):
        b["owner"].decide(approval,decision(b,approval,"rejected"))


def test_rejection_cancels_without_delivery(integrity):
    b=integrity
    approval,draft=draft_approval(b)
    result=b["owner"].decide(approval,decision(b,approval,"rejected"))
    assert result["run"]["status"] == "cancelled"
    assert result["run"]["messages_sent_by_app"] == 0
    assert b["conn"].execute("SELECT status FROM soloestate.followup_drafts WHERE id=%s",(draft,)).fetchone()[0] == "rejected"


def test_wrong_hash_and_changed_recipient_cannot_be_approved(integrity):
    b=integrity
    approval,_=draft_approval(b)
    with pytest.raises(RunConflict,match="Reviewed payload"):
        b["owner"].decide(approval,DecisionRequest(decision="approved",expected_payload_hash="0"*64,idempotency_key=str(uuid4())))
    b["conn"].execute("UPDATE soloestate.leads SET email=%s WHERE id=%s",(f"{uuid4()}@example.invalid",b["lead"]))
    with pytest.raises(RunConflict,match="stale"):
        b["owner"].decide(approval,decision(b,approval))
    assert b["owner"].review(approval)["status"] == "pending"


def test_database_blocks_snapshot_draft_and_unrecorded_decision_tampering(integrity):
    b=integrity
    approval,draft=draft_approval(b)
    for query,args in [
        ("UPDATE soloestate.approvals SET payload='{}' WHERE id=%s",(approval,)),
        ("UPDATE soloestate.approvals SET status='approved',decided_at=clock_timestamp() WHERE id=%s",(approval,)),
        ("UPDATE soloestate.followup_drafts SET body='Changed',version=2 WHERE id=%s",(draft,)),
        ("UPDATE soloestate.followup_drafts SET status='sent' WHERE id=%s",(draft,)),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation):
            with b["conn"].transaction():
                b["conn"].execute(query,args)


def test_booked_window_cannot_be_edited(integrity):
    b=integrity
    approval=booking_approval(b)
    b["owner"].decide(approval,decision(b,approval))
    result=b["tools"].execute("book_site_visit",{"approval_id":approval,"idempotency_key":"book"},ToolContext(run_id=b["run"]))
    assert result.status == "succeeded", result
    with pytest.raises(psycopg.errors.CheckViolation):
        with b["conn"].transaction():
            b["conn"].execute("UPDATE soloestate.visit_slots SET starts_at=starts_at+interval '1 hour',ends_at=ends_at+interval '1 hour' WHERE id=%s",(b["slot"],))


def test_terminal_run_replays_existing_action_but_refuses_new_action(integrity):
    b=integrity
    approval,draft=draft_approval(b)
    b["owner"].decide(approval,decision(b,approval))
    b["conn"].execute("UPDATE soloestate.runs SET status='completed' WHERE id=%s",(b["run"],))
    args={"lead_id":str(b["lead"]),"subject":"Fictional review","body":"Never send this fixture","idempotency_key":"draft"}
    replay=b["tools"].execute("create_followup_draft",args,ToolContext(run_id=b["run"]))
    assert replay.status == "succeeded" and replay.data["created"] is False
    assert b["tools"].execute("create_followup_draft",{**args,"idempotency_key":"new"},ToolContext(run_id=b["run"])).code == "run_closed"


def test_approval_then_resume_uses_real_owner_evidence(integrity):
    b=integrity
    def request_actual_draft(history):
        draft_id=json.loads(history[-1]["output"])["data"]["draft"]["id"]
        return call("request_approval",{"kind":"followup","draft_id":draft_id,"idempotency_key":"approval"})
    model=Model(plan("create_followup_draft","request_approval"),call("create_followup_draft",{"lead_id":str(b["lead"]),"subject":"Fictional","body":"Draft only","idempotency_key":"draft"}),request_actual_draft)
    report=asyncio.run(AgentRunner(model,store=b["store"],tools=b["tools"],settings=Settings(_env_file=None)).run(RunRequest(goal="Phase7 rollback resume test",request_key=str(uuid4()))))
    approval=report["verified_steps"][-1]["result"]["data"]["approval"]["id"]
    b["owner"].decide(approval,decision(b,approval))
    resumed=asyncio.run(AgentRunner(Model(done(1,2)),store=RunStore(b["factory"]),tools=b["tools"],settings=Settings(_env_file=None)).resume(report["run_id"]))
    assert resumed["status"] == "completed"
    assert resumed["draft_counts"] == {"approved":1}
    assert resumed["owner_decisions"][0]["decision"] == "approved"
    assert resumed["messages_sent_by_app"] == 0


@pytest.mark.parametrize("committed",[False,True])
def test_restart_reconciles_pending_or_committed_step(integrity,committed):
    b=integrity
    request=RunRequest(goal="Phase7 rollback recovery test",request_key=str(uuid4()),mode="read_only")
    row,_=b["store"].create(request)
    args={"lead_id":str(b["lead"])}
    proposal=Plan.model_validate(json.loads(plan("get_property_matches").text))
    pending={"call_id":"interrupted-call","name":"get_property_matches","arguments":json.dumps(args)}
    b["store"].checkpoint(row["id"],[],[],plan=proposal,mode="read_only",state={"pending_calls":[pending],"cursor":0})
    step=b["store"].start_step(row["id"],1,pending["call_id"],pending["name"],args)
    if committed:
        b["tools"].execute(pending["name"],args,ToolContext(run_id=row["id"]),on_result=lambda conn,result:b["store"].complete_step(conn,step,result))
    b["conn"].execute("UPDATE soloestate.runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(row["id"],))
    class NoReplay:
        def execute(self,*args,**kwargs):
            pytest.fail("A committed step must not execute twice")
    resumed=asyncio.run(AgentRunner(Model(done(1)),store=RunStore(b["factory"]),tools=NoReplay() if committed else b["tools"],settings=Settings(_env_file=None)).resume(row["id"],recover=True))
    assert resumed["status"] == "completed",resumed
    assert len(resumed["verified_steps"]) == 1


def test_active_lease_and_old_worker_are_fenced(integrity):
    b=integrity
    row,_=b["store"].create(RunRequest(goal="Phase7 rollback lease test",request_key=str(uuid4())))
    args={"lead_id":str(b["lead"]),"subject":"Fenced write","body":"Must not persist","idempotency_key":"fenced"}
    step=b["store"].start_step(row["id"],1,"old-call","create_followup_draft",args)
    other=RunStore(b["factory"])
    with pytest.raises(RunConflict,match="still active"):
        other.claim(row["id"],recover=True)
    b["conn"].execute("UPDATE soloestate.runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(row["id"],))
    other.claim(row["id"],recover=True)
    with pytest.raises(RunConflict,match="lease was lost"):
        b["store"].checkpoint(row["id"],[],[])
    with pytest.raises(RunConflict,match="lease was lost"):
        b["tools"].execute("create_followup_draft",args,ToolContext(run_id=row["id"]),
            on_result=lambda conn,result:b["store"].complete_step(conn,step,result))
    assert b["conn"].execute("SELECT count(*) FROM soloestate.followup_drafts WHERE run_id=%s",(row["id"],)).fetchone()[0] == 0


def test_pending_approval_and_resume_limit_block_execution(integrity):
    b=integrity
    draft_approval(b)
    with pytest.raises(RunConflict,match="still pending"):
        b["store"].claim(b["run"])
    b["conn"].execute("UPDATE soloestate.runs SET status='failed',resume_count=3 WHERE id=%s",(b["other"],))
    with pytest.raises(RunConflict,match="limit reached"):
        b["store"].claim(b["other"],recover=True)


def parallel(actions):
    barrier=Barrier(len(actions))
    def execute(action):
        barrier.wait(timeout=10)
        try:
            return action()
        except RunConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(execute,actions))


def test_independent_sessions_cannot_double_book(concurrent_fixture):
    b=concurrent_fixture
    first,second=booking_approval(b),booking_approval(b,b["other"])
    for approval in (first,second):
        b["owner"].decide(approval,decision(b,approval))
    def book(run,approval):
        return b["tools"].execute("book_site_visit",{"approval_id":approval,"idempotency_key":"book"},ToolContext(run_id=run))
    results=parallel([lambda:book(b["run"],first),lambda:book(b["other"],second)])
    assert sum(result.status=="succeeded" for result in results)==1,results
    assert sum(result.code=="slot_unavailable" for result in results)==1,results


def test_independent_sessions_replay_same_booking(concurrent_fixture):
    b=concurrent_fixture
    approval=booking_approval(b)
    b["owner"].decide(approval,decision(b,approval))
    def book():
        return b["tools"].execute("book_site_visit",{"approval_id":approval,"idempotency_key":"same-book"},ToolContext(run_id=b["run"]))
    results=parallel([book,book])
    assert all(r.status=="succeeded" for r in results),[r.model_dump() for r in results]
    assert {r.data["created"] for r in results} == {True,False}
    assert len({r.data["booking"]["id"] for r in results})==1


def test_conflicting_owner_decisions_have_one_winner(concurrent_fixture):
    b=concurrent_fixture
    approval,_=draft_approval(b)
    yes,no=decision(b,approval),decision(b,approval,"rejected")
    results=parallel([lambda:b["owner"].decide(approval,yes),lambda:b["owner"].decide(approval,no)])
    assert results.count("conflict")==1,results
    assert sum(isinstance(r,dict) for r in results)==1


def test_independent_sessions_claim_one_worker(concurrent_fixture):
    b=concurrent_fixture
    results=parallel([lambda:RunStore().claim(b["run"]),lambda:RunStore().claim(b["run"])])
    assert results.count("conflict")==1,results
    assert sum(isinstance(r,tuple) and r[1] is True for r in results)==1
