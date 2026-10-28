"""Durable run and step evidence. Business successes and step results commit together."""

from contextlib import nullcontext
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import Settings
from app.database import connect


class RunConflict(Exception):
    pass


class RunStore:
    def __init__(self, connection_factory=None):
        self.connect = connection_factory or (lambda: connect(Settings()))
        self.leases = {}

    def _guard(self, conn, run_id):
        token = self.leases.get(str(run_id))
        row = conn.execute("""UPDATE soloestate.runs SET lease_until=clock_timestamp()+interval '12 minutes'
            WHERE id=%s AND lease_token=%s AND status='running' RETURNING id""", (run_id, token)).fetchone()
        if row is None:
            raise RunConflict("Worker lease was lost; inspect the run before retrying.")

    def create(self, request):
        token = uuid4()
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("""INSERT INTO soloestate.runs (id,request_key,goal,status,plan,lease_token,lease_until)
                    VALUES (%s,%s,%s,'running',%s,%s,clock_timestamp()+interval '12 minutes')
                    ON CONFLICT (request_key) DO NOTHING RETURNING *""",
                    (uuid4(), request.request_key, request.goal, Jsonb({"policy": {"mode": request.mode}}), token)).fetchone()
                fresh = row is not None
                if not fresh:
                    row = cur.execute("SELECT * FROM soloestate.runs WHERE request_key=%s", (request.request_key,)).fetchone()
                    if row["goal"] != request.goal or (row["plan"] or {}).get("policy", {}).get("mode") != request.mode:
                        raise RunConflict("Request key is already bound to a different goal or permission mode.")
                    if row["status"] in {"pending", "running"}:
                        raise RunConflict("Run is already in progress or ready to resume; use explicit resume/recover.")
                    if row["outcome"] is None:
                        raise RunConflict("Existing run has no verified outcome; manual review is required.")
        if fresh:
            self.leases[str(row["id"])] = token
        return row, fresh

    def claim(self, run_id, *, recover=False):
        token = uuid4()
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("SELECT *,lease_until>clock_timestamp() AS active FROM soloestate.runs WHERE id=%s FOR UPDATE", (run_id,)).fetchone()
                if row is None:
                    raise RunConflict("Run was not found.")
                if row['origin'] != 'agent':
                    raise RunConflict('Owner CRM operations cannot be resumed as AI runs.')
                if row["status"] in {"completed", "cancelled"}:
                    if row["outcome"] is None:
                        raise RunConflict("Closed run has no verified report; manual inspection is required.")
                    return row, False
                pending = cur.execute("SELECT 1 FROM soloestate.approvals WHERE run_id=%s AND status='pending' LIMIT 1", (run_id,)).fetchone()
                if pending:
                    raise RunConflict("Owner decisions are still pending; execution cannot resume.")
                if row["status"] == "running" and row["active"]:
                    raise RunConflict("Worker lease is still active; recovery would risk duplicate execution.")
                if row["status"] in {"running", "failed"} and not recover:
                    raise RunConflict("Interrupted/failed runs require explicit recover after the lease expires.")
                if row["resume_count"] >= 3:
                    raise RunConflict("Resume/recovery limit reached (three activations after initial execution).")
                if (row["plan"] or {}).get("policy", {}).get("mode") not in {"read_only", "draft_first"}:
                    raise RunConflict("Legacy run has no trusted permission policy; create a new owner goal.")
                row = cur.execute("""UPDATE soloestate.runs SET status='running',lease_token=%s,
                    lease_until=clock_timestamp()+interval '12 minutes',resume_count=resume_count+1,
                    updated_at=clock_timestamp() WHERE id=%s RETURNING *""", (token, run_id)).fetchone()
        self.leases[str(run_id)] = token
        return row, True

    def checkpoint(self, run_id, trace, events, plan=None, mode="draft_first", state=None):
        with self.connect() as conn:
            self._guard(conn, run_id)
            if plan is not None:
                conn.execute("UPDATE soloestate.runs SET plan=%s,updated_at=clock_timestamp() WHERE id=%s",
                             (Jsonb({"policy": {"mode": mode}, "proposal": plan.model_dump(mode="json")}), run_id))
            conn.execute("UPDATE soloestate.runs SET outcome=%s,updated_at=clock_timestamp() WHERE id=%s",
                         (Jsonb({"status": "running", "model_trace": trace, "events": events}), run_id))
            if state is not None:
                conn.execute("UPDATE soloestate.runs SET execution_state=%s WHERE id=%s", (Jsonb(state), run_id))

    def start_step(self, run_id, sequence, call_id, name, arguments):
        step_id = uuid4()
        with self.connect() as conn:
            self._guard(conn, run_id)
            previous = conn.execute("SELECT id,tool_name,arguments,status FROM soloestate.run_steps WHERE run_id=%s AND idempotency_key=%s", (run_id, call_id)).fetchone()
            if previous:
                if previous[1] != name or previous[2] != arguments or previous[3] != "running":
                    raise RunConflict("Step identity changed or already has committed evidence.")
                return previous[0]
            conn.execute("""INSERT INTO soloestate.run_steps
                (id,run_id,sequence,tool_name,idempotency_key,arguments,status)
                VALUES (%s,%s,%s,%s,%s,%s,'running')""",
                (step_id, run_id, sequence, name, call_id, Jsonb(arguments)))
        return step_id

    def complete_step(self, conn, step_id, result):
        run_id = conn.execute("SELECT run_id FROM soloestate.run_steps WHERE id=%s", (step_id,)).fetchone()[0]
        self._guard(conn, run_id)
        state = "failed" if result.status == "error" else ("awaiting_approval" if result.status == "approval_required" else "succeeded")
        error = {"code": result.code, "message": result.message} if result.status == "error" else None
        cursor = conn.execute("""UPDATE soloestate.run_steps SET status=%s,result=%s,error=%s,
            finished_at=clock_timestamp() WHERE id=%s AND status='running'""",
            (state, Jsonb(result.model_dump(mode="json")), Jsonb(error) if error else None, step_id))
        if cursor.rowcount != 1:
            raise psycopg.ProgrammingError("Step evidence could not be committed.")

    def failed_step(self, step_id, result):
        with self.connect() as conn:
            self.complete_step(conn, step_id, result)

    def finish(self, run_id, status, reason, trace, events, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.connect()) as conn:
            if str(run_id) in self.leases:
                self._guard(conn, run_id)
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute("SELECT id,sequence,tool_name,status,result,error FROM soloestate.run_steps WHERE run_id=%s ORDER BY sequence", (run_id,)).fetchall()
                counts = {}
                for table in ("followup_drafts", "approvals", "bookings"):
                    records = cur.execute(sql.SQL("SELECT status,count(*) AS count FROM soloestate.{} WHERE run_id=%s GROUP BY status").format(sql.Identifier(table)), (run_id,)).fetchall()
                    counts[table] = {record["status"]: record["count"] for record in records}
                if counts["approvals"].get("pending", 0):
                    status, reason = "awaiting_approval", "owner_approval_required"
                decisions = cur.execute("""SELECT d.id,d.approval_id,d.decision,d.actor,d.payload_hash,d.decided_at
                    FROM soloestate.approval_decisions d JOIN soloestate.approvals a ON a.id=d.approval_id
                    WHERE a.run_id=%s ORDER BY d.decided_at,d.id""", (run_id,)).fetchall()
                report = {"run_id": str(run_id), "status": status, "reason": reason,
                          "completion_scope": "validated_plan_execution; overall business-goal fulfillment is not independently certified",
                          "model_trace": trace, "events": events,
                          "verified_steps": [{**row, "id": str(row["id"])} for row in rows],
                          "draft_counts": counts["followup_drafts"], "approval_counts": counts["approvals"],
                          "booking_counts": counts["bookings"], "messages_sent_by_app": 0}
                report["owner_decisions"] = [{**d,"id":str(d["id"]),"approval_id":str(d["approval_id"]),"decided_at":d["decided_at"].isoformat()} for d in decisions]
                cur.execute("UPDATE soloestate.runs SET status=%s,outcome=%s,lease_token=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE id=%s",
                            (status, Jsonb(report), run_id))
        return report

    def resume_context(self, run_id):
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                steps = cur.execute("SELECT * FROM soloestate.run_steps WHERE run_id=%s ORDER BY sequence", (run_id,)).fetchall()
                approvals = cur.execute("SELECT id,status,kind FROM soloestate.approvals WHERE run_id=%s", (run_id,)).fetchall()
        return steps, {str(a["id"]): a["status"] for a in approvals}

    def status(self, run_id):
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("""SELECT id,goal,request_key,status,plan,outcome,resume_count,lease_until,
                    coalesce(lease_until>clock_timestamp(),false) AS worker_active FROM soloestate.runs WHERE id=%s""", (run_id,)).fetchone()
                if row is None:
                    raise RunConflict("Run was not found.")
                steps=cur.execute("""SELECT id,sequence,tool_name,status,arguments,result,error,created_at,finished_at
                    FROM soloestate.run_steps WHERE run_id=%s ORDER BY sequence LIMIT 201""",(run_id,)).fetchall()
                row["steps_truncated"]=len(steps)>200
                row["live_steps"]=[{**step,"id":str(step["id"]),"created_at":step["created_at"].isoformat(),
                    "finished_at":step["finished_at"].isoformat() if step["finished_at"] else None} for step in steps[:200]]
        return {**row, "id": str(row["id"]), "lease_until":row["lease_until"].isoformat() if row["lease_until"] else None}

    def find(self, request_key):
        with self.connect() as conn:
            row=conn.execute("SELECT id FROM soloestate.runs WHERE request_key=%s",(request_key,)).fetchone()
            if row is None:
                raise RunConflict("Request key was not found.")
        return self.status(row[0])
