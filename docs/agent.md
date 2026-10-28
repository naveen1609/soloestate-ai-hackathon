# Bounded Meta orchestration

The internal agent now uses real Meta Responses API calls to propose a structured plan, select native function calls, observe actual PostgreSQL tool results, adapt to failures, and stop at a validated finish, owner approval, or a bounded failure condition. No production mock path exists.

## Run the demonstrable flow

Use the already configured backend Meta and PostgreSQL credentials. No new settings are required.

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m scripts.run_agent --request-key my-phase6-demo-1
Write-Output "Agent exit code: $LASTEXITCODE"
```

This makes multiple potentially billable model requests and persists a fictional run, tool steps, draft and pending approval. The default goal explicitly reviews the 2–8 November 2026 demo week, compares lead/property matches, reads appointment availability, drafts one personalized follow-up, requests owner approval, and stops. It cannot send messages. Booking still requires an approved persisted request; owner decisions are now available through the authenticated Phase 7 interface (see owner-recovery.md).

Expected: exit 0, `status: awaiting_approval`, one draft, one pending approval, zero bookings, and `messages_sent_by_app: 0`. The report includes the real run ID, provider response IDs/models, validation events, and actual persisted tool steps/results. The model may take a different valid path; failures and bounded stops are reported honestly. If the fixed appointment dates have elapsed, available-slot results can legitimately be empty.

Reusing **the same goal, permission mode, and request key** returns the existing stopped/terminal report without new model calls or duplicate actions. Use a new request key only when you intentionally want a new run. A running key is rejected to prevent a second worker; Phase 7 now resumes approved work and explicitly recovers failed/expired workers; see owner-recovery.md for the lease and retry guards.

Read the persisted plan and current outcome without a model call:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --status YOUR_RUN_UUID
```

Expected: JSON containing `id`, actual persisted `status`, the proposed `plan`, and backend-generated `outcome`. Replace the UUID placeholder with a returned real run ID.

For a read-only goal, enforce capabilities at the server boundary:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --read-only --request-key my-read-review-1 --goal 'Review fictional leads created during 2-8 November 2026 in Asia/Kolkata and compare their property matches. Do not draft or request approvals.'
```

Expected: exit 0 and `status: completed` after verified read steps. Read-only mode rejects mutation calls regardless of model output. A completed run means the model's validated required tool plan finished with supporting evidence; it is not independent certification that every interpretation of the owner's business goal was achieved.

## Loop and validation

1. Validate the goal/request key/mode. Create a committed running record with a unique request key, or retrieve its stopped report.
2. Request a JSON plan with permitted tool names, purposes, required-step flags, and approval policy. Pydantic rejects extra fields, invalid tools, and malformed plans. One plan repair is allowed.
3. Replay prior assistant output and opaque encrypted reasoning with `store=False`; ask for registered native function calls. Tools have fixed JSON schemas and server-supplied run context.
4. Validate each native call and JSON argument object. Duplicate JSON keys, nonfinite numbers, unsupported tools, forbidden modes, repeated call IDs, unknown arguments, and invalid record identifiers cannot become arbitrary execution.
5. Persist a running step. The tool's successful business write and succeeded/awaiting-approval step result commit in one transaction. Failure rolls back the business transaction and persists a failed step separately. Success is returned only after commit.
6. Append the actual tool result and backend step sequence as a native `function_call_output`. On ordinary success/failure, the next model turn sees that result before deciding what to do. Errors consume a bounded failure budget.
7. Stop immediately for a pending approval; do not ask the model to execute later calls in the same batch. The pending result is persisted for owner review. Phase 7 now supports explicit resume after authenticated decisions.
8. To finish, the model must return a small completion contract listing actual succeeded step sequences. Required plan tools must have succeeded, and a failed tool must have a later successful recovery of that tool before normal completion. The complete timeline retains failures; recovery does not turn failed attempts into successes.
9. Build the final report from committed steps and actual database counts. Model-written outcome narratives are never displayed as verified facts. Any remaining pending approval forces `awaiting_approval` even if the model tries to finish.

The plan is a model proposal, not proof of execution. Its semantic completeness against an arbitrary natural-language goal is not independently guaranteed. Completion evidence checks refer to tool identities and sequence outcomes, not a general proof that all business requirements were satisfied. Reports preserve this scope explicitly.

Meta's [Responses API protocol](https://dev.meta.ai/docs/protocols/responses) documents typed function-call outputs and stateless encrypted reasoning replay. Replay exists only in memory in Phase 6; encrypted/private reasoning is not logged or persisted. Database records are sent as business data, not trusted instructions. SQL, shell commands and arbitrary function names are never executable model capabilities.

## Bounds and error states

Optional backend environment variables (defaults):

```dotenv
AGENT_MAX_TURNS=12
AGENT_MAX_TOOL_CALLS=16
AGENT_MAX_FAILURES=3
AGENT_TIMEOUT_SECONDS=180
```

Validated maximums are 30 model turns, 50 tool calls, 5 failures, and 600 seconds. Model turns include planning and repairs. A provider response contains at most four native calls; execution is serial and approval stops the batch. History is capped at 500,000 UTF-8 JSON bytes and stops instead of silently dropping evidence. Each model response is capped at 4096 output tokens and must be completed; provider/transport failure has no automatic retry.

The deadline bounds model waits and is checked before calls. In-flight synchronous database transactions are allowed to finish under the existing connection/statement/lock timeouts, so evidence is not published while a cancelled background write might still commit. The overall wall time can therefore exceed the budget by an in-flight database operation and final persistence. No background worker/queue is added.

Persisted failed reasons include `invalid_plan`, `unsupported_completion` events followed by the failure budget, `model_turn_limit`, `tool_call_limit`, `failure_limit`, `history_limit`, `deadline`, `duplicate_call_id`, `model_blocked`, and safe provider/database codes. CLI exits 1 for failed/unconfirmed execution; exit 0 for completed or correctly paused approval. Exit 0 at approval does not mean messages or bookings occurred.

If persistence fails, CLI reports `status: unconfirmed`; it never substitutes a fabricated success report. Inspect the persisted run before retrying. An interrupted process can leave running state until its lease expires. Phase 7 now adds authenticated owner routes, durable pending calls, explicit bounded resume/recovery, decision history and database lifecycle guards; see owner-recovery.md.

## Verification commands

Unit/default suite (database cases skip without opt-ins):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: **128 passed, 55 skipped**.

Full suite including rollback-only real Supabase tool and orchestration tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
```

Expected: **178 passed, 5 skipped** and no broken dependencies. The five remaining skips are the original dedicated-empty-database schema tests. The new agent tests mock provider responses, but use real PostgreSQL tools and persistence inside forced-rollback transactions. They verify persisted plans/steps, result-driven recovery, approval stopping with a real draft ID, duplicate worker/replay behavior, and atomic rollback when evidence persistence fails. The separately executed live demo uses the real model and retains its fictional draft/approval for review.

Only agent persistence tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m agent_database --run-agent-db-tests
```

Expected: **13 passed**. Run live database tests sequentially. The dashboard uses this same orchestrator through owner-authenticated routes.

If a command fails, share its safe JSON report, run ID, exit code, and exact command. Do not share `.env`, API keys, connection strings, raw SDK debug logs, or encrypted reasoning. For `running`/`unconfirmed` state, use `--status` before starting another run.

Phase 7 update: request-key lookup (`--lookup-key KEY`) can recover the run ID after a lost response. Status now includes lease expiry, worker-active state and resume count. Budgets apply per activation with a hard maximum of three resumes. Only confirmed-rollback SQLSTATEs receive one extra tool transaction attempt; uncertain commits and provider requests are not automatically retried.
