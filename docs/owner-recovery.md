# Phase 7: Owner decisions, integrity, and recovery

Owner review/decision endpoints, a local owner CLI, durable pending tool calls, fenced worker leases, explicit bounded resume/recovery, and stronger PostgreSQL guards are implemented. The owner credential was generated locally in ignored `backend/.env` without displaying it. Existing Meta/database/TLS configuration remains valid. No setup action is needed to finish this phase.

## Review and decide

From the backend directory:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m scripts.owner pending
.\.venv\Scripts\python.exe -m scripts.owner review YOUR_APPROVAL_UUID
```

`pending` lists at most 100 pending requests, with explicit truncation. `review` returns the exact snapshot, target kind, run ID, current status and SHA-256 `payload_hash`. No message or booking is committed by reviewing.

Only after reviewing and choosing a decision, run one of these commands, substituting the real ID/hash and a unique decision key:

```powershell
.\.venv\Scripts\python.exe -m scripts.owner decide YOUR_APPROVAL_UUID --decision approved --payload-hash HASH_FROM_REVIEW --request-key my-owner-decision-1
# Or reject instead:
.\.venv\Scripts\python.exe -m scripts.owner decide YOUR_APPROVAL_UUID --decision rejected --payload-hash HASH_FROM_REVIEW --request-key my-owner-decision-1
```

Expected: exit 0 and the committed owner decision. An identical retry with the same key replays the decision; a different decision or reused key for another target is rejected. The reviewed hash must still match. Approval also revalidates current content/version/recipient or current booking details and availability. A stale snapshot must be replaced with a fresh request, not edited in place.

Approved follow-ups become **approved drafts**, not delivered emails. There is no sender integration. Approved booking requests do not book during the decision call; execution must resume and recheck availability. A rejection cancels the run once no other approvals remain pending. No model tool can decide approval, forge the actor, invoke HTTP endpoints, or read the credential.

The retained Phase 6 demo approval has intentionally **not** been approved or rejected automatically. Your choice is a product action, not a configuration blocker.

## Resume or recover

After approval:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --resume YOUR_RUN_UUID
```

Expected: a bounded continuation using persisted goal, permission mode, plan, actual results and owner decisions. The run either completes its validated plan, pauses for another review, or reports a specific failure. Messages remain unsent. Existing completed/cancelled runs replay their report without further execution.

If the original response/run ID was lost, find it without a model call using `python -m scripts.run_agent --lookup-key YOUR_REQUEST_KEY`. Status includes lease expiry, worker-active state and resume count. Inspect an interrupted or failed run before recovery:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --status YOUR_RUN_UUID
.\.venv\Scripts\python.exe -m scripts.run_agent --resume YOUR_RUN_UUID --recover
```

Recovery is explicit, not a background scheduler. A running worker's lease must have expired; the API/CLI provides no force-override of an active lease. Leases last 12 minutes and renew at checkpoints and guarded tool transactions. The old worker's token is fenced from subsequent evidence/business commits after takeover. Pending owner decisions block resume. A run permits at most **three** resumptions/recoveries after initial execution, enforced by the database and claim logic.

Each activation retains the configured model/tool/failure/time budgets (defaults: 12/16/3/180 seconds). At most four activations are possible, so recovery cannot reset budgets indefinitely. Provider calls have no automatic retry. The tool layer permits **one** extra transaction attempt only for PostgreSQL serialization failure (`40001`), deadlock (`40P01`), or lock-wait failure (`55P03`), which confirm rollback. Network errors, query cancellation and uncertain commit responses are not automatically retried.

The pending model-call batch is committed before tool execution. Tool business changes and successful step evidence still commit together. On recovery, a committed step is replayed from its evidence rather than executed again; an interrupted running step can reuse the same step ID and action idempotency key. Private reasoning is not persisted: resumed model context is reconstructed from the validated plan, committed tool results and owner facts. Ordinary in-process turns still replay encrypted reasoning in memory.

Recovered completion resolves historical awaiting-approval steps against actual owner decisions while preserving their original timeline results. Failed attempts remain failed; no report rewrites them as original successes. Database/persistence uncertainty returns `unconfirmed` or a conflict and requires inspection before another activation.

## Authenticated HTTP interface

FastAPI exposes these owner-only routes:

| Route | Behavior |
| --- | --- |
| `GET /owner/approvals` | Pending review snapshots (bounded list) |
| `GET /owner/approvals/{id}` | One review snapshot and hash |
| `POST /owner/approvals/{id}/decision` | Authenticated approved/rejected decision with reviewed hash and idempotency key |
| `POST /owner/runs` | Start a validated owner goal |
| `GET /owner/runs` | Latest 20 run identifiers, or lookup by request_key |
| `GET /owner/runs/{id}` | Read persisted plan/status/outcome |
| `POST /owner/runs/{id}/resume` | Resume; JSON `{ "recover": true }` explicitly requests stale/failed recovery |

All require `Authorization: Bearer <OWNER_API_TOKEN>`; missing, invalid or unconfigured credentials fail closed. The credential is a high-entropy server secret, never returned in API responses or placed in `NEXT_PUBLIC_` variables. Health remains unauthenticated. The local owner CLI reads the ignored backend environment file and relies on the local operating-system account's access to that file; it never prints the token. `python -m scripts.owner init` is idempotent and creates a credential only when absent.

This is single-owner hackathon authentication, not multi-user account management. Use loopback for the local demo; a public deployment needs HTTPS and a suitably restricted database runtime role. The dashboard/login flow is Phase 8; no frontend API key embedding is added here.

Errors return 401 for authentication, 409 for state/idempotency/lease conflicts, 422 for invalid inputs, and 503 with a safe `unconfirmed` response for database uncertainty. Raw driver bodies, passwords and SDK debug logs are not returned.

## Database invariants

Migration `0002_execution_integrity.sql` adds execution state/lease/resume fields and private RLS-enabled `approval_decisions`.

- Approval identity, snapshot and request key cannot change; pending requests cannot become approved/rejected without matching decision evidence. A consumed booking approval cannot be reused.
- Reviewed draft content/version is immutable. Owner evidence is required for approved/rejected draft state, and setting a message to sent is blocked because delivery does not exist.
- Booking inserts require a matching owner-approved request, current enabled future slot, active property, matching recipient/title/price/times and decision evidence. The confirmed-slot unique index remains the final duplicate barrier.
- Confirmed appointment windows cannot be moved or deleted; booking identity cannot be retargeted. Terminal runs replay existing idempotent actions but reject new mutations.
- All eleven tables remain in the private schema with RLS; no browser grants/policies are added. Database administration credentials remain trusted and are not an authorization boundary against a malicious database administrator. Decision audit rows are append-only through the service API; no update/delete API is exposed.

Migration setup is already applied and verified locally. For a new clone/project, use the existing `scripts.database migrate` then `seed` and `verify`; migration history is immutable and checksummed.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
```

Expected: **156 passed, 5 skipped**, with no broken dependencies. Default tests without database opt-ins expect **120 passed, 41 skipped**. The integrity opt-in includes rollback-only owner/recovery tests plus independent-session concurrency tests. Concurrency requires briefly committed isolated fictional records, then exact-ID cleanup in `finally`; it never uses the original pending demo approval. Run these checks sequentially, not with pytest-xdist. The original five empty-database schema tests still require a separate TEST_DATABASE_URL.

If a command fails, share the command, safe error JSON, run/approval ID and exit code. Never share `.env`, bearer credentials, database URIs/passwords or SDK debug logs. A pending approval is an intentional stop; review it instead of bypassing the guard.
