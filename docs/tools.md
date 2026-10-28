# Business tools

The backend now has seven typed tools in `app/tools/service.py`. `tool_definitions()` supplies the explicit function allowlist and JSON argument schemas for the Phase 6 orchestrator. `BusinessTools.execute(name, arguments, ToolContext(run_id=...))` validates inputs, executes parameterized PostgreSQL operations, validates outputs, and returns a structured result only after the connection commits. The run context is supplied by server code, never by a model argument.

Tools remain internal fixed handlers. The real-model orchestrator reaches them through authenticated owner execution, and the Next.js dashboard presents their actual results. Owner decisions use a separate authenticated boundary; no message sender exists. See agent.md and dashboard.md.

## Operations

| Tool | Arguments | Verified behavior |
| --- | --- | --- |
| `get_leads` | `since`, `until`, optional `limit` | Read persisted leads created within `[since,until)`; newest first, stable ID tie-break, truncation flag |
| `get_property_matches` | `lead_id`, optional `limit` | Read active inventory within the lead's budget, in a preferred locality, with at least the requested bedrooms |
| `get_available_slots` | `since`, `until`, optional `limit` | Read future enabled slots fully within the window and without a confirmed booking; return database check time and recheck warning |
| `create_followup_draft` | `lead_id`, `subject`, `body`, `idempotency_key` | Insert an email draft associated with the server's run; never send a message |
| `request_approval` | `kind`, `idempotency_key`, plus a target | Insert a pending approval and reviewable snapshot; never approve it |
| `book_site_visit` | `approval_id`, `idempotency_key` | Use only an approved booking request in the server's run, recheck and lock current data, reject changed details/unavailable slots, insert booking and consume approval atomically |
| `get_run_status` | Empty object | Read persisted run state, latest 100 steps in chronological order, truncation flag, and actual draft/approval/booking status counts |

All inputs reject unknown fields. A model cannot set `status`, choose another `run_id`, set an email recipient directly, approve a request, or invoke SQL/shell execution. UUIDs, timezone-aware windows, text sizes, positive limits, and approval target combinations are validated with Pydantic. Date windows must be positive and no longer than 31 days. Limits are capped; truncation is explicit rather than implying all rows were returned.

Matching is deterministic, not a model-generated score: 100 for exact requested bedrooms, 90 for additional bedrooms, ordered by that preference then lowest price and stable ID. Every result already meets budget and locality requirements. Empty inventory yields an empty successful result, never an invented match. These scores describe property fit, not calibrated conversion probabilities or full business lead prioritization.

Follow-up approval requires `kind: followup` and `draft_id` only. The draft must belong to the context's run and still be a draft. Its content, version, recipient and channel are snapshotted. Booking approval requires `kind: booking` with `lead_id`, `property_id`, and `slot_id`. It snapshots recipient, property title/price, and slot times. Other target combinations fail before database access.

## Result contract and safety

Results have `tool`, `status`, `data`, `code`, and `message`. Status is `succeeded`, `approval_required`, or `error`. `data` is serialized from that tool's typed output. A newly requested pending approval returns `approval_required`; a booking attempt without approved consent returns an error with code `approval_required`. A result records what the operation actually did, not completion of the owner's overall goal.

Draft output explicitly reports `customer_message_sent: false`. No sender integration exists. A booking output includes the real database booking ID and status. `get_run_status` never converts a running run into completed based on model text. Standalone tool calls do not create timeline entries by default. The Phase 6 runner now supplies a server-only audit callback that commits successful `run_steps` evidence atomically with business writes.

Mutation tools serialize same-run writes using a transaction-scoped advisory lock. Per-run idempotency keys replay the same persisted draft/approval/booking with `created: false`; reusing a key with different arguments is rejected. Terminal runs reject new mutations. Booking locks its approval and slot, takes shared locks on current lead/property data, checks server time and availability, and compares the current snapshot to the approved one. Rejected, pending, consumed, cross-run, wrong-kind, stale, disabled, elapsed, inactive-property, and already-booked targets cannot create a new visit. The schema's unique confirmed-slot index is the final duplicate-booking barrier.

Connection/commit failures never produce a success response. Known errors return safe codes such as `not_found`, `invalid_arguments`, `unknown_tool`, `idempotency_conflict`, `run_closed`, `slot_unavailable`, `stale_approval`, `invalid_approval`, `approval_required`, `conflict`, and `database_error`. Driver messages and credentials are not returned. Phase 7 adds one extra attempt only for confirmed-rollback SQLSTATEs; ambiguous connection/commit failures are never automatically retried.

The internal executor must be called without an ambient transaction in production so its connection context owns the commit. Tests deliberately inject savepoint-backed connections inside a rollback fixture. Future orchestration must not publish provisional results before an outer transaction commits.

## Run the read-only demo check

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m scripts.check_tools
Write-Output "Tool check exit code: $LASTEXITCODE"
```

With the unchanged seed and its appointments still in the future, expected output is `status: ok`, all seven registry names, `read_only: true`, **3 leads in the fixed demo week, 1 match for the first seed lead, and 6 available slots**. Counts reflect the database at execution time; legitimate data changes or elapsed appointment dates may change them. The fixed demo week is 2–8 November 2026 in Asia/Kolkata. This script does not call the model, create a run, create drafts, request approvals, or book anything.

## Automated verification

Default, without database test opt-ins:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: **128 passed, 55 skipped**. The skips are 16 tool cases, 5 agent cases, 15 owner/recovery/concurrency cases, and the original 5 empty-database schema tests.

Run the full suite including rollback-only tool tests against the configured Supabase application schema:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
```

Expected: **178 passed, 5 skipped** and no broken dependencies. The 5 remaining skips require a separate empty `TEST_DATABASE_URL`, as documented in `database.md`. To run only tool integration cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m tools_database --run-tool-db-tests
```

Expected: **16 passed**. Run these tests sequentially, not with pytest-xdist. They require the current three migrations and a trusted database connection. They create uniquely named fictional contacts, inventory, future slots and runs inside transactions; simulate decisions only in the fixture; then roll back all changes. No customer messages are sent and no test booking is retained. These tests cover individual tool behavior and sequential contention; they do not establish full multi-process orchestration recovery or concurrent-owner approval behavior.

If anything fails, share the exact command and safe test failure/error output. Never share `.env`, connection strings, API keys, or raw driver debug logs.

## Owner lifecycle

Phase 6 now wires these definitions and actual result envelopes into the bounded real Meta orchestration loop, with persisted plans and steps. Phase 7 now implements authenticated owner decisions, immutable approval lifecycle guards, bounded recovery, terminal-run replay, real independent-session concurrency checks and confirmed-slot edit protection. See owner-recovery.md. Owner decisions remain outside the tool registry. The agent must never be given approval authority.
