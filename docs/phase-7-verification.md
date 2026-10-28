# Phase 7 verification — 27 October 2026

Implemented:

- Authenticated owner review, pending lists, payload-hash-bound decisions, stable decision idempotency keys, and owner audit evidence.
- Local owner credential initialization without displaying the secret; ignored backend/.env remains the credential source.
- Owner-only FastAPI run/status/lookup/decision/resume routes and CLI review/decide/resume/recover/lookup controls.
- Durable pending native calls before execution, committed-step reconciliation, resumption from actual database facts, worker lease tokens and stale-worker fencing.
- A hard three-resume/recovery limit, existing per-activation resource budgets, and one extra DB transaction attempt only for confirmed-rollback SQLSTATEs.
- Database guards for approval snapshots/transitions, reviewed draft content/version, owner decision evidence, fresh booking details, and confirmed slot edits.
- Existing action replay on terminal runs without admitting new mutations.

Migration `0002_execution_integrity.sql` applied successfully; the original migration and seed were unchanged. New fields and the private RLS-enabled approval_decisions table are present. No frontend or dependency changes were required.

## Automated evidence

Executed from backend/:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
```

Final result: **156 passed, 5 skipped**, one upstream Starlette/AnyIO deprecation warning, in **408.78 seconds**. No broken requirements. The five remaining skips are the original schema checks requiring a dedicated empty TEST_DATABASE_URL; they are not claimed as executed.

New cases include 13 owner authentication/API/hash cases, 15 real PostgreSQL owner/recovery/concurrency cases, rollback-only retry cases, and parsing the new SQL migration. Existing tool/agent/model/health tests remain in the full suite. One earlier concurrent replay check returned a transient database error; the isolated rerun passed. Safe SQLSTATE diagnostics and bounded rollback-only retry handling were added, and the final complete suite passed. No automatic retry is performed for unknown connection/commit outcomes.

Real DB coverage includes:

- Review hash mismatch, recipient drift, duplicate/conflicting decisions, rejection/cancellation and unsent approved drafts.
- Database rejection of snapshot/content tampering, unrecorded consent and fake sent status.
- Confirmed slot edit rejection and terminal action replay without new actions.
- Approved continuation using actual owner evidence; unfinished-step recovery and post-commit cursor reconciliation.
- Active-lease rejection, fenced stale-worker checkpoints/writes and bounded resume counts.
- Independent database sessions racing for one slot, replaying one booking key, approving versus rejecting, and claiming one worker. Exactly one booking/decision/worker wins where required; identical booking retries return one persisted booking ID.

Rollback-only cases leave no data. Concurrency cases use separately committed isolated fictional UUID records and remove only their own records in finally cleanup; no original demo approval is used for a test decision.

## Live real-Meta recovery probe

Real retained read-only run: **8ff4b3e9-d24f-425c-8581-e5947c4a2dcc**, request key **phase7-live-recovery-20261027-v1**.

The probe used the real Meta client and a model-generated plan. A developer-only interruption was injected after the first real read tool and its evidence committed, before queue advancement. The known exited probe's lease was expired explicitly, then a new normal worker resumed with recover enabled. This injection is a test harness, not a production bypass endpoint or a mock AI response.

Confirmed:

- Status **completed**, reason `validated_plan_finished`.
- **Three actual provider responses**, one resume, one succeeded get_leads step.
- Original first-step ID and finished timestamp **unchanged** after recovery.
- No draft, booking or message created/sent by the probe.
- Request-key lookup returned that completed run with resume_count 1, lease_until null and worker_active false.

Read without further model calls:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --lookup-key phase7-live-recovery-20261027-v1
.\.venv\Scripts\python.exe -m scripts.run_agent --status 8ff4b3e9-d24f-425c-8581-e5947c4a2dcc
```

## Owner boundary and retained demo

The original Phase 6 run **608afe1f-33e9-4ee7-b1c0-4fa9cb225252** remains awaiting owner approval. It was not automatically approved, rejected, sent or booked. Authenticated live approval listing returned HTTP 200 and found its pending review; unauthenticated access returned HTTP 401. The owner credential was not displayed or embedded in frontend code.

Owner run lookup was also manually verified with authentication and without it. Final cleanup/seed verification is recorded alongside this change: test fixture records are removed and the original 4 leads / 3 properties / 6 slots remain. Two migrations are applied. The retained demo runs are intentional evidence, separate from test fixtures.

## Limits

Authentication is a single-owner server bearer credential suitable for the local hackathon, not multi-user account management. Database administration credentials remain trusted; service APIs do not expose audit update/delete. Resume is explicit and lease-gated, not a scheduler or background job queue. Public deployment still needs HTTPS and a restricted database runtime role. No message delivery integration exists; approved drafts remain unsent. Dashboard/login UX is Phase 8.

No new configuration or decision is required from the owner to complete Phase 7 verification. Reviewing and deciding the original pending draft is an optional product action. See owner-recovery.md for exact commands, expected results and safe failure reporting. No commits or pushes were made.
