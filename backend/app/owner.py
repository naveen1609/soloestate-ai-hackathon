"""Owner-only consent boundary. Not registered as a model tool."""

from hashlib import sha256
from hmac import compare_digest
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from psycopg.rows import dict_row

from app.agent.store import RunConflict, RunStore
from app.config import Settings
from app.database import connect
from app.tools.service import _booking_snapshot, ToolFailure, require_contact_action


def authenticate_owner(token: str | None, settings: Settings):
    secret = settings.owner_api_token
    if not secret or len(secret.get_secret_value()) < 32:
        raise PermissionError("Owner authentication is not configured.")
    if not token or not compare_digest(token.encode(), secret.get_secret_value().encode()):
        raise PermissionError("Owner authentication failed.")


def payload_hash(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision: Literal["approved", "rejected"]
    expected_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=128)


class OwnerService:
    def __init__(self, connection_factory=None):
        self.connect = connection_factory or (lambda: connect(Settings()))

    def review(self, approval_id: UUID):
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("SELECT * FROM soloestate.approvals WHERE id=%s", (approval_id,)).fetchone()
                if row is None:
                    raise RunConflict("Approval was not found.")
        return {"id": str(row["id"]), "run_id": str(row["run_id"]), "kind": row["kind"], "status": row["status"],
                "payload": row["payload"], "payload_hash": payload_hash(row["payload"])}

    def decide(self, approval_id: UUID, request: DecisionRequest):
        request = DecisionRequest.model_validate(request)
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                target = cur.execute("SELECT run_id FROM soloestate.approvals WHERE id=%s", (approval_id,)).fetchone()
                if target is None:
                    raise RunConflict("Approval was not found.")
                # Consistent order: run -> approval -> draft/slot. Decisions cannot race an active worker.
                run = cur.execute("SELECT *,lease_until>clock_timestamp() AS active FROM soloestate.runs WHERE id=%s FOR UPDATE", (target["run_id"],)).fetchone()
                row = cur.execute("SELECT * FROM soloestate.approvals WHERE id=%s FOR UPDATE", (approval_id,)).fetchone()
                digest = payload_hash(row["payload"])
                if not compare_digest(digest, request.expected_payload_hash):
                    raise RunConflict("Reviewed payload changed; review the approval again.")
                existing = cur.execute("SELECT * FROM soloestate.approval_decisions WHERE idempotency_key=%s OR approval_id=%s", (request.idempotency_key, approval_id)).fetchall()
                if existing:
                    if len(existing) != 1 or str(existing[0]["approval_id"]) != str(approval_id) or existing[0]["decision"] != request.decision or existing[0]["idempotency_key"] != request.idempotency_key or existing[0]["payload_hash"] != digest:
                        raise RunConflict("Decision/idempotency conflict; this request was already decided.")
                    return {"approval_id": str(approval_id), "decision": existing[0]["decision"], "replayed": True}
                if run["status"] == "running" and run["active"]:
                    raise RunConflict("Run is executing; wait until it pauses for approval.")
                if row["status"] != "pending" or run["status"] in {"completed", "cancelled"}:
                    raise RunConflict("This approval no longer accepts an owner decision.")
                draft = None
                if row["kind"] == "followup":
                    draft = cur.execute("SELECT * FROM soloestate.followup_drafts WHERE id=%s FOR UPDATE", (row["draft_id"],)).fetchone()
                    lead = cur.execute("SELECT * FROM soloestate.leads WHERE id=%s FOR SHARE", (row["lead_id"],)).fetchone()
                    if request.decision == 'approved':
                        try:
                            require_contact_action(lead)
                        except ToolFailure as exc:
                            raise RunConflict(str(exc)) from None
                    if request.decision=='approved' and lead['archived_at'] is not None:
                        raise RunConflict('Lead is archived; restore it before approving a follow-up.')
                    current = {"draft_id": str(draft["id"]), "lead_id": str(draft["lead_id"]), "version": draft["version"],
                               "channel": draft["channel"], "recipient": lead["email"], "subject": draft["subject"], "body": draft["body"]}
                    if request.decision == "approved" and (draft["status"] != "draft" or current != row["payload"]):
                        raise RunConflict("Draft details are stale; create a fresh draft and approval.")
                elif request.decision == "approved":
                    try:
                        current = _booking_snapshot(cur, row["lead_id"], row["property_id"], row["slot_id"])
                    except ToolFailure as exc:
                        raise RunConflict(str(exc)) from None
                    if current != row["payload"]:
                        raise RunConflict("Booking details changed; request fresh approval.")
                decision = cur.execute("""INSERT INTO soloestate.approval_decisions
                    (id,approval_id,idempotency_key,decision,payload_hash,actor)
                    VALUES (%s,%s,%s,%s,%s,'owner') RETURNING decided_at""",
                    (uuid4(), approval_id, request.idempotency_key, request.decision, digest)).fetchone()
                cur.execute("UPDATE soloestate.approvals SET status=%s,decided_at=%s WHERE id=%s",
                            (request.decision, decision["decided_at"], approval_id))
                if draft and draft["status"] == "draft":
                    cur.execute("UPDATE soloestate.followup_drafts SET status=%s WHERE id=%s", (request.decision, draft["id"]))
                rejected = cur.execute("SELECT 1 FROM soloestate.approvals WHERE run_id=%s AND status='rejected' LIMIT 1", (row["run_id"],)).fetchone()
                report = RunStore(self.connect).finish(row["run_id"], "cancelled" if rejected else "pending",
                    "owner_rejected" if rejected else "owner_approved_resume_required",
                    (run["outcome"] or {}).get("model_trace", []), (run["outcome"] or {}).get("events", []), connection=conn)
        return {"approval_id": str(approval_id), "decision": request.decision, "replayed": False, "run": report}
