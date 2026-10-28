"""Internal business-tool boundary. No dynamic SQL or model-supplied executable names."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from app.config import Settings, EnvironmentFileError
from app.database import connect, DatabaseConfigurationError
from app.tools import schemas as s


class ToolFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def required(row, label="Record"):
    if row is None:
        raise ToolFailure("not_found", f"{label} was not found.")
    return row


def get_leads(cur, args: s.GetLeadsInput, context):
    rows = cur.execute("""SELECT * FROM soloestate.leads
        WHERE archived_at IS NULL AND created_at >= %s AND created_at < %s
        ORDER BY created_at DESC, id LIMIT %s""", (args.since, args.until, args.limit + 1)).fetchall()
    return s.LeadsOutput(leads=rows[:args.limit], truncated=len(rows) > args.limit)


def get_property_matches(cur, args: s.GetMatchesInput, context):
    lead = required(cur.execute("SELECT * FROM soloestate.leads WHERE id=%s AND archived_at IS NULL", (args.lead_id,)).fetchone(), "Active lead")
    rows = cur.execute("""SELECT id,title,locality,price,bedrooms,description,
        CASE WHEN bedrooms=%s THEN 100 ELSE 90 END AS score
        FROM soloestate.properties WHERE active AND price BETWEEN %s AND %s
        AND bedrooms >= %s AND lower(locality) = ANY(%s::text[])
        ORDER BY CASE WHEN bedrooms=%s THEN 0 ELSE 1 END, price, id LIMIT %s""",
        (lead["bedrooms"], lead["budget_min"], lead["budget_max"], lead["bedrooms"],
         [locality.lower() for locality in lead["preferred_localities"]], lead["bedrooms"], args.limit + 1)).fetchall()
    for row in rows:
        row["reasons"] = ["within_budget", "preferred_locality", "exact_bedrooms" if row["bedrooms"] == lead["bedrooms"] else "additional_bedrooms"]
    return s.MatchesOutput(lead_id=args.lead_id, matches=rows[:args.limit], truncated=len(rows) > args.limit)


def get_available_slots(cur, args: s.GetSlotsInput, context):
    checked_at = cur.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    rows = cur.execute("""SELECT s.id,s.starts_at,s.ends_at FROM soloestate.visit_slots s
        WHERE s.enabled AND s.starts_at >= %s AND s.starts_at >= %s AND s.ends_at <= %s
        AND NOT EXISTS (SELECT 1 FROM soloestate.bookings b WHERE b.slot_id=s.id AND b.status='confirmed')
        ORDER BY s.starts_at,s.id LIMIT %s""", (checked_at, args.since, args.until, args.limit + 1)).fetchall()
    return s.SlotsOutput(slots=rows[:args.limit], truncated=len(rows) > args.limit, checked_at=checked_at)


def _mutable_run(cur, context):
    # Serialize same-run mutations, including concurrent idempotency-key reuse.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"soloestate:run:{context.run_id}",))
    row = required(cur.execute("SELECT status FROM soloestate.runs WHERE id=%s FOR UPDATE", (context.run_id,)).fetchone(), "Run")
    return row["status"]


def _require_open(status):
    if status in {"completed", "failed", "cancelled"}:
        raise ToolFailure("run_closed", "This run no longer accepts mutations.")


def _same(condition):
    if not condition:
        raise ToolFailure("idempotency_conflict", "This idempotency key is already bound to different arguments.")


def create_followup_draft(cur, args: s.DraftInput, context):
    status = _mutable_run(cur, context)
    row = cur.execute("SELECT * FROM soloestate.followup_drafts WHERE run_id=%s AND idempotency_key=%s",
                      (context.run_id, args.idempotency_key)).fetchone()
    if row:
        _same(row["lead_id"] == args.lead_id and row["subject"] == args.subject and row["body"] == args.body)
        return s.DraftOutput(draft=row, created=False)
    _require_open(status)
    required(cur.execute("SELECT id FROM soloestate.leads WHERE id=%s AND archived_at IS NULL FOR SHARE", (args.lead_id,)).fetchone(), "Active lead")
    row = cur.execute("""INSERT INTO soloestate.followup_drafts
        (id,run_id,lead_id,idempotency_key,subject,body) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
        (uuid4(), context.run_id, args.lead_id, args.idempotency_key, args.subject, args.body)).fetchone()
    return s.DraftOutput(draft=row, created=True)


def _booking_snapshot(cur, lead_id, property_id, slot_id):
    # Lock in the same order for request and commit. Slot lock serializes bookers across runs.
    slot = required(cur.execute("SELECT * FROM soloestate.visit_slots WHERE id=%s FOR UPDATE", (slot_id,)).fetchone(), "Slot")
    prop = required(cur.execute("SELECT * FROM soloestate.properties WHERE id=%s FOR SHARE", (property_id,)).fetchone(), "Property")
    lead = required(cur.execute("SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE", (lead_id,)).fetchone(), "Lead")
    if lead['archived_at'] is not None:
        raise ToolFailure('lead_unavailable', 'The lead is archived.')
    now = cur.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    if not slot["enabled"] or slot["starts_at"] <= now:
        raise ToolFailure("slot_unavailable", "The appointment slot is disabled or has already started.")
    if cur.execute("SELECT id FROM soloestate.bookings WHERE slot_id=%s AND status='confirmed'", (slot_id,)).fetchone():
        raise ToolFailure("slot_unavailable", "The appointment slot has already been booked.")
    if not prop["active"]:
        raise ToolFailure("property_unavailable", "The property is no longer active.")
    # Only business data relevant to the approved action; no volatile timestamps.
    return {"lead_id": str(lead_id), "recipient": lead["email"], "property_id": str(property_id),
            "property_title": prop["title"], "price": str(prop["price"]), "slot_id": str(slot_id),
            "starts_at": slot["starts_at"].isoformat(), "ends_at": slot["ends_at"].isoformat()}


def request_approval(cur, args: s.ApprovalInput, context):
    status = _mutable_run(cur, context)
    row = cur.execute("SELECT * FROM soloestate.approvals WHERE run_id=%s AND idempotency_key=%s",
                      (context.run_id, args.idempotency_key)).fetchone()
    if row:
        _same(row["kind"] == args.kind and row["draft_id"] == args.draft_id and
              (args.kind == "followup" or (row["lead_id"], row["property_id"], row["slot_id"]) == (args.lead_id, args.property_id, args.slot_id)))
        return s.ApprovalOutput(approval=row, created=False)
    _require_open(status)
    if args.kind == "followup":
        draft = required(cur.execute("SELECT * FROM soloestate.followup_drafts WHERE id=%s AND run_id=%s FOR UPDATE",
                                     (args.draft_id, context.run_id)).fetchone(), "Draft in this run")
        if draft["status"] != "draft":
            raise ToolFailure("invalid_state", "Only a draft can be submitted for follow-up approval.")
        lead = required(cur.execute("SELECT email FROM soloestate.leads WHERE id=%s AND archived_at IS NULL FOR SHARE", (draft["lead_id"],)).fetchone(), "Active lead")
        lead_id = draft["lead_id"]
        payload = {"draft_id": str(draft["id"]), "lead_id": str(lead_id), "version": draft["version"],
                   "channel": draft["channel"], "recipient": lead["email"], "subject": draft["subject"], "body": draft["body"]}
    else:
        lead_id = args.lead_id
        payload = _booking_snapshot(cur, lead_id, args.property_id, args.slot_id)
    row = cur.execute("""INSERT INTO soloestate.approvals
        (id,run_id,kind,lead_id,draft_id,property_id,slot_id,idempotency_key,payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (uuid4(), context.run_id, args.kind, lead_id, args.draft_id, args.property_id, args.slot_id,
         args.idempotency_key, Jsonb(payload))).fetchone()
    # No tool can set status=approved. Owner decisions are a separate Phase 7 boundary.
    return s.ApprovalOutput(approval=row, created=True)


def book_site_visit(cur, args: s.BookInput, context):
    status = _mutable_run(cur, context)
    previous = cur.execute("SELECT * FROM soloestate.bookings WHERE run_id=%s AND idempotency_key=%s",
                           (context.run_id, args.idempotency_key)).fetchone()
    if previous:
        _same(previous["approval_id"] == args.approval_id)
        return s.BookingOutput(booking=previous, created=False)
    _require_open(status)
    approval = required(cur.execute("SELECT * FROM soloestate.approvals WHERE id=%s AND run_id=%s FOR UPDATE",
                                    (args.approval_id, context.run_id)).fetchone(), "Approval in this run")
    if approval["kind"] != "booking":
        raise ToolFailure("invalid_approval", "This approval is not for a booking.")
    if approval["status"] != "approved":
        raise ToolFailure("approval_required", "Booking requires an owner-approved request; pending, rejected or consumed approvals cannot be used.")
    payload = _booking_snapshot(cur, approval["lead_id"], approval["property_id"], approval["slot_id"])
    if payload != approval["payload"]:
        raise ToolFailure("stale_approval", "The approved booking details changed; request fresh owner approval.")
    row = cur.execute("""INSERT INTO soloestate.bookings
        (id,run_id,approval_id,lead_id,property_id,slot_id,idempotency_key,replaces_booking_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (uuid4(), context.run_id, approval["id"], approval["lead_id"], approval["property_id"], approval["slot_id"], args.idempotency_key, context.replaces_booking_id)).fetchone()
    cur.execute("UPDATE soloestate.approvals SET status='consumed' WHERE id=%s", (approval["id"],))
    return s.BookingOutput(booking=row, created=True)


def get_run_status(cur, args: s.RunStatusInput, context):
    run = required(cur.execute("SELECT * FROM soloestate.runs WHERE id=%s", (context.run_id,)).fetchone(), "Run")
    steps = cur.execute("SELECT * FROM soloestate.run_steps WHERE run_id=%s ORDER BY sequence DESC LIMIT 101", (context.run_id,)).fetchall()
    counts = {}
    for table in ("followup_drafts", "approvals", "bookings"):
        rows = cur.execute(sql.SQL("SELECT status,count(*) AS count FROM soloestate.{} WHERE run_id=%s GROUP BY status").format(sql.Identifier(table)), (context.run_id,)).fetchall()
        counts[table] = {row["status"]: row["count"] for row in rows}
    return s.RunStatusOutput(run=run, steps=list(reversed(steps[:100])), steps_truncated=len(steps) > 100,
                             draft_counts=counts["followup_drafts"], approval_counts=counts["approvals"], booking_counts=counts["bookings"])


@dataclass(frozen=True)
class ToolSpec:
    description: str
    arguments: type[BaseModel]
    output: type[BaseModel]
    handler: Callable


# Explicit references only. Never resolve a model-provided function with getattr/eval.
REGISTRY = {
    "get_leads": ToolSpec("Read leads created within a specified half-open date window.", s.GetLeadsInput, s.LeadsOutput, get_leads),
    "get_property_matches": ToolSpec("Read deterministic exact-criteria property matches for one lead.", s.GetMatchesInput, s.MatchesOutput, get_property_matches),
    "get_available_slots": ToolSpec("Read future enabled slots without confirmed bookings; availability is provisional.", s.GetSlotsInput, s.SlotsOutput, get_available_slots),
    "create_followup_draft": ToolSpec("Persist an email draft only; does not send a customer message.", s.DraftInput, s.DraftOutput, create_followup_draft),
    "request_approval": ToolSpec("Persist a pending review snapshot; never approve it. For followup use ONLY kind, draft_id and idempotency_key (omit lead_id/property_id/slot_id). For booking use kind, lead_id, property_id, slot_id and idempotency_key (omit draft_id).", s.ApprovalInput, s.ApprovalOutput, request_approval),
    "book_site_visit": ToolSpec("Commit a site visit only from an approved, unchanged request after locking and rechecking availability.", s.BookInput, s.BookingOutput, book_site_visit),
    "get_run_status": ToolSpec("Read persisted run state, recent steps and counts; do not infer completion.", s.RunStatusInput, s.RunStatusOutput, get_run_status),
}


def tool_definitions() -> list[dict]:
    return [{"type": "function", "name": name, "description": spec.description,
             "parameters": spec.arguments.model_json_schema()} for name, spec in REGISTRY.items()]


class BusinessTools:
    def __init__(self, connection_factory=None):
        self._connect = connection_factory or (lambda: connect(Settings()))

    def execute(self, name: str, arguments: dict, context: s.ToolContext, *, on_result=None) -> s.ToolResult:
        # Retry only states that confirm rollback, never ambiguous transport/commit loss.
        for attempt in range(2):
            try:
                return self._execute_once(name, arguments, context, on_result=on_result)
            except psycopg.Error as exc:
                if attempt == 1:
                    return s.ToolResult(tool=name,status="error",code="database_error",
                        message=f"Database transaction retry limit reached; no new action is confirmed. SQLSTATE {exc.sqlstate}.")

    def _execute_once(self, name: str, arguments: dict, context: s.ToolContext, *, on_result=None) -> s.ToolResult:
        spec = REGISTRY.get(name)
        if spec is None:
            return s.ToolResult(tool=name, status="error", code="unknown_tool", message="Tool is not registered.")
        try:
            args = spec.arguments.model_validate(arguments)
            context = s.ToolContext.model_validate(context)
        except ValidationError:
            return s.ToolResult(tool=name, status="error", code="invalid_arguments", message="Tool arguments or server run context failed validation.")
        try:
            with self._connect() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    output = spec.output.model_validate(spec.handler(cur, args, context))
                    data = output.model_dump(mode="json")
                    status = "approval_required" if isinstance(output, s.ApprovalOutput) and output.approval.status == "pending" else "succeeded"
                    result = s.ToolResult(tool=name, status=status, data=data)
                    if on_result is not None:
                        # Server-only audit callback shares the business transaction.
                        on_result(conn, result)
            # Connection context has committed. Never return success before this point.
            return result
        except ToolFailure as exc:
            return s.ToolResult(tool=name, status="error", code=exc.code, message=str(exc))
        except (DatabaseConfigurationError, EnvironmentFileError):
            return s.ToolResult(tool=name, status="error", code="configuration", message="Backend database configuration is invalid.")
        except psycopg.IntegrityError:
            return s.ToolResult(tool=name, status="error", code="conflict", message="A database integrity constraint rejected the action; no new action is confirmed.")
        except psycopg.Error as exc:
            if exc.sqlstate in {"40001", "40P01", "55P03"}:
                raise
            detail = f" SQLSTATE {exc.sqlstate}." if exc.sqlstate else ""
            return s.ToolResult(tool=name, status="error", code="database_error", message="Database operation failed; no new action is confirmed." + detail)
        except ValidationError:
            return s.ToolResult(tool=name, status="error", code="invalid_result", message="Backend result validation failed; no new action is confirmed.")
