"""Opt-in real PostgreSQL tool checks. All test writes roll back, including approvals."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest

from app.config import Settings
from app.database import connect
from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools
from app.owner import OwnerService, DecisionRequest

pytestmark = pytest.mark.tools_database


@pytest.fixture
def business(request):
    if not request.config.getoption("--run-tool-db-tests"):
        pytest.skip("Use --run-tool-db-tests for rollback-only Supabase tool tests")
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            run_id, lead, prop, slot = uuid4(), uuid4(), uuid4(), uuid4()
            locality = f"Fictional Tool Test {uuid4()}"
            start = conn.execute("SELECT greatest(coalesce(max(ends_at),CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)+interval '365 days' FROM soloestate.visit_slots").fetchone()[0]
            conn.execute("INSERT INTO soloestate.runs (id,request_key,goal,status) VALUES (%s,%s,'Rollback-only fictional tool test','running')", (run_id, str(uuid4())))
            conn.execute("""INSERT INTO soloestate.leads (id,name,email,preferred_localities,budget_min,budget_max,bedrooms,created_at)
                VALUES (%s,'Fictional Tool Test',%s,%s,100,200,2,%s)""", (lead, f"{uuid4()}@example.invalid", [locality], start))
            conn.execute("INSERT INTO soloestate.properties (id,title,locality,price,bedrooms) VALUES (%s,'Fictional Test Property',%s,150,2)", (prop, locality))
            conn.execute("INSERT INTO soloestate.visit_slots (id,starts_at,ends_at) VALUES (%s,%s,%s)", (slot, start, start + timedelta(minutes=30)))

            @contextmanager
            def factory():
                # Tool commits are savepoint releases here; outer fixture always rolls back.
                with conn.transaction():
                    yield conn

            context = ToolContext(run_id=run_id)
            tools = BusinessTools(factory)

            def call(name, args):
                return tools.execute(name, args, context)

            def approve(approval_id, status="approved"):
                service = OwnerService(factory)
                review = service.review(approval_id)
                service.decide(approval_id, DecisionRequest(decision=status, expected_payload_hash=review["payload_hash"], idempotency_key=str(uuid4())))

            yield {"conn": conn, "run": run_id, "lead": lead, "property": prop, "slot": slot,
                   "start": start, "call": call, "approve": approve, "tools": tools, "context": context}


def booking_approval(b, key="request-booking"):
    result = b["call"]("request_approval", {"kind": "booking", "lead_id": str(b["lead"]), "property_id": str(b["property"]), "slot_id": str(b["slot"]), "idempotency_key": key})
    assert result.status == "approval_required", result
    return result.data["approval"]["id"]


def test_leads_filter_and_matching(business):
    b = business
    result = b["call"]("get_leads", {"since": b["start"].isoformat(), "until": (b["start"]+timedelta(days=1)).isoformat()})
    assert result.status == "succeeded", result
    assert [lead["id"] for lead in result.data["leads"]] == [str(b["lead"])]
    matches = b["call"]("get_property_matches", {"lead_id": str(b["lead"])})
    assert matches.status == "succeeded", matches
    assert matches.data["matches"][0]["id"] == str(b["property"])
    assert matches.data["matches"][0]["score"] == 100


def test_matching_no_exact_inventory_is_empty(business):
    b = business
    b["conn"].execute("UPDATE soloestate.leads SET budget_min=1,budget_max=10 WHERE id=%s", (b["lead"],))
    result = b["call"]("get_property_matches", {"lead_id": str(b["lead"])})
    assert result.status == "succeeded"
    assert result.data["matches"] == []


def test_matching_order_and_result_limit(business):
    b = business
    locality = b["conn"].execute("SELECT locality FROM soloestate.properties WHERE id=%s", (b["property"],)).fetchone()[0]
    b["conn"].execute("INSERT INTO soloestate.properties (id,title,locality,price,bedrooms) VALUES (%s,'Additional bedroom',%s,120,3)", (uuid4(), locality))
    b["conn"].execute("INSERT INTO soloestate.properties (id,title,locality,price,bedrooms,active) VALUES (%s,'Inactive',%s,110,2,false)", (uuid4(), locality))
    result = b["call"]("get_property_matches", {"lead_id": str(b["lead"]), "limit": 1})
    assert result.status == "succeeded", result
    assert result.data["matches"][0]["id"] == str(b["property"])
    assert result.data["truncated"] is True


def test_missing_lead_reports_not_found(business):
    result = business["call"]("get_property_matches", {"lead_id": str(uuid4())})
    assert result.code == "not_found"


def test_draft_is_persisted_without_sending_and_idempotent(business):
    b = business
    args = {"lead_id": str(b["lead"]), "idempotency_key": "draft-1", "subject": "Fictional follow-up", "body": "A draft only; no customer delivery."}
    first = b["call"]("create_followup_draft", args)
    assert first.status == "succeeded", first
    assert first.data["draft"]["status"] == "draft"
    assert first.data["customer_message_sent"] is False
    again = b["call"]("create_followup_draft", args)
    assert again.data["created"] is False
    assert again.data["draft"]["id"] == first.data["draft"]["id"]
    conflict = b["call"]("create_followup_draft", {**args, "body": "Changed"})
    assert conflict.code == "idempotency_conflict"


def test_followup_approval_contains_reviewable_snapshot(business):
    b = business
    draft = b["call"]("create_followup_draft", {"lead_id": str(b["lead"]), "idempotency_key": "draft", "subject": "Review this", "body": "Fictional body"})
    args = {"kind": "followup", "draft_id": draft.data["draft"]["id"], "idempotency_key": "approval"}
    result = b["call"]("request_approval", args)
    assert result.status == "approval_required", result
    assert result.data["approval"]["status"] == "pending"
    assert result.data["approval"]["payload"]["body"] == "Fictional body"
    assert result.data["approval"]["payload"]["version"] == 1
    assert b["call"]("request_approval", args).data["created"] is False
    b["approve"](result.data["approval"]["id"])
    booking = b["call"]("book_site_visit", {"approval_id": result.data["approval"]["id"], "idempotency_key": "wrong-kind"})
    assert booking.code == "invalid_approval"


@pytest.mark.parametrize("decision", ["pending", "rejected"])
def test_nonapproved_booking_refused(business, decision):
    b = business
    approval = booking_approval(b)
    if decision != "pending":
        b["approve"](approval, decision)
    result = b["call"]("book_site_visit", {"approval_id": approval, "idempotency_key": "booking"})
    assert result.code == ("run_closed" if decision == "rejected" else "approval_required")
    assert b["conn"].execute("SELECT count(*) FROM soloestate.bookings WHERE run_id=%s", (b["run"],)).fetchone()[0] == 0


def test_approved_booking_and_replay(business):
    b = business
    approval = booking_approval(b)
    b["approve"](approval)
    args = {"approval_id": approval, "idempotency_key": "booking"}
    result = b["call"]("book_site_visit", args)
    assert result.status == "succeeded", result
    assert result.data["booking"]["status"] == "confirmed"
    assert b["call"]("book_site_visit", args).data["created"] is False
    assert b["call"]("book_site_visit", {**args,"idempotency_key":"another-key"}).code == "approval_required"
    assert b["conn"].execute("SELECT status FROM soloestate.approvals WHERE id=%s", (approval,)).fetchone()[0] == "consumed"
    slots = b["call"]("get_available_slots", {"since": b["start"].isoformat(), "until": (b["start"]+timedelta(hours=1)).isoformat()})
    assert slots.status == "succeeded"
    assert str(b["slot"]) not in [slot["id"] for slot in slots.data["slots"]]


def test_slot_taken_after_approval_is_refused(business):
    b = business
    first, second = booking_approval(b, "first"), booking_approval(b, "second")
    b["approve"](first)
    b["approve"](second)
    assert b["call"]("book_site_visit", {"approval_id": first, "idempotency_key": "first"}).status == "succeeded"
    assert b["call"]("book_site_visit", {"approval_id": second, "idempotency_key": "second"}).code == "slot_unavailable"


def test_disabled_slot_after_approval_is_refused(business):
    b = business
    approval = booking_approval(b)
    b["approve"](approval)
    b["conn"].execute("UPDATE soloestate.visit_slots SET enabled=false WHERE id=%s", (b["slot"],))
    assert b["call"]("book_site_visit", {"approval_id": approval, "idempotency_key": "booking"}).code == "slot_unavailable"


def test_elapsed_slot_is_neither_listed_nor_booked(business):
    b = business
    approval = booking_approval(b)
    b["approve"](approval)
    past = b["conn"].execute("SELECT min(starts_at)-interval '365 days' FROM soloestate.visit_slots").fetchone()[0]
    b["conn"].execute("UPDATE soloestate.visit_slots SET starts_at=%s,ends_at=%s WHERE id=%s", (past, past+timedelta(minutes=30), b["slot"]))
    slots = b["call"]("get_available_slots", {"since": past.isoformat(), "until": (past+timedelta(hours=1)).isoformat()})
    assert slots.status == "succeeded"
    assert slots.data["slots"] == []
    assert b["call"]("book_site_visit", {"approval_id": approval, "idempotency_key": "booking"}).code == "slot_unavailable"


def test_changed_approved_details_are_refused(business):
    b = business
    approval = booking_approval(b)
    b["approve"](approval)
    b["conn"].execute("UPDATE soloestate.properties SET price=175 WHERE id=%s", (b["property"],))
    assert b["call"]("book_site_visit", {"approval_id": approval, "idempotency_key": "booking"}).code == "stale_approval"
    assert b["conn"].execute("SELECT status FROM soloestate.approvals WHERE id=%s", (approval,)).fetchone()[0] == "approved"


def test_cross_run_approval_cannot_be_used(business):
    b = business
    approval = booking_approval(b)
    b["approve"](approval)
    other_run = uuid4()
    b["conn"].execute("INSERT INTO soloestate.runs (id,request_key,goal,status) VALUES (%s,%s,'Other fictional run','running')", (other_run, str(uuid4())))
    result = b["tools"].execute("book_site_visit", {"approval_id": approval, "idempotency_key": "booking"}, ToolContext(run_id=other_run))
    assert result.code == "not_found"


def test_run_status_reports_persisted_state_and_counts(business):
    b = business
    b["call"]("create_followup_draft", {"lead_id": str(b["lead"]), "idempotency_key": "draft", "subject": "Test", "body": "Draft"})
    booking_approval(b)
    result = b["call"]("get_run_status", {})
    assert result.status == "succeeded", result
    assert result.data["run"]["status"] == "running"
    assert result.data["run"]["outcome"] is None
    assert result.data["draft_counts"] == {"draft": 1}
    assert result.data["approval_counts"] == {"pending": 1}
    assert result.data["booking_counts"] == {}


def test_terminal_run_refuses_new_writes(business):
    b = business
    b["conn"].execute("UPDATE soloestate.runs SET status='completed' WHERE id=%s", (b["run"],))
    result = b["call"]("create_followup_draft", {"lead_id": str(b["lead"]), "idempotency_key": "draft", "subject": "Test", "body": "Draft"})
    assert result.code == "run_closed"
