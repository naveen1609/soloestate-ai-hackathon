# Phase 6 verification — 27 October 2026

Implemented a CLI-driven real Meta agent loop, using the existing schema and credentials. No new migration, runtime dependency, public execution endpoint, or frontend change was required.

## Features verified

- JSON plan proposal with Pydantic validation, permitted tool names, explicit required steps and approval policy.
- Native Meta Responses function calls, typed argument validation, safe allowlist dispatch, and actual `function_call_output` feedback.
- Stateless encrypted reasoning replay in memory; no reasoning logging or persistence.
- Backend-only permission modes and run context; read-only mode cannot be overridden by model output.
- Bounded turns, tool calls, failures, plan repair, history size, and execution deadline.
- Committed run/plan/checkpoint metadata, running steps, actual results/errors, and final reports.
- Successful business changes and tool-step evidence share a transaction through a server-only audit callback.
- Immediate owner-approval stop, evidence-based finish validation, backend-derived final counts, and no model-written success narrative.
- Request-key replay without new model calls or duplicate actions; second in-progress workers are rejected.

## Automated tests

Executed from backend/:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests
.\.venv\Scripts\python.exe -m pip check
```

Result: **124 passed, 5 skipped**, one upstream Starlette/AnyIO deprecation warning, in **139.55 seconds**. Dependency integrity passed. The remaining skips are the original schema tests requiring a separate empty TEST_DATABASE_URL.

The suite includes 25 agent unit/protocol cases, 5 real Supabase agent persistence cases, and the 17 existing real Supabase tool cases. Unit/protocol cases mock the provider, including actual SDK HTTP transport tests. Database agent cases use real tools and SQL, while provider decisions are mocked; all fixture writes roll back.

Covered cases include plan repair/invalid-plan rejection, actual result feedback, tool-error adaptation, approval stops, unregistered and forbidden tools, malformed/duplicate-key JSON, unsupported completion claims, absent evidence, failure/turn/tool/history/deadline limits, repeated call IDs, provider timeout, database failure, encrypted reasoning/function-output replay, persisted plan/step data, replay without model calls, and atomic rollback of a draft when audit persistence fails.

After the final suite, read-only SQL confirmed **zero leftover fixture runs and zero leftover fixture leads**. Seed verification still returned four original demo leads, three properties, six slots and one applied migration.

## Real Meta + Supabase demonstration

Executed the default demo goal with:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --request-key phase6-demo-20261027-v1
```

Real retained run: **608afe1f-33e9-4ee7-b1c0-4fa9cb225252**.

Confirmed result:

- Status: **awaiting_approval**, reason `owner_approval_required`, CLI exit code 0.
- Actual provider: **muse-spark-1.3**, **11 responses**, with response IDs in the persisted model trace.
- **10 persisted tool steps**: seven succeeded, two failed input validation, and one awaiting owner approval.
- The model read three demo-week leads, evaluated their real property matches, read actual available slots, and created one personalized follow-up draft.
- Two approval attempts supplied invalid target combinations. The model observed actual error feedback, read persisted run status, corrected the arguments, and created the pending approval. Failed attempts remain failed in the timeline.
- One committed draft, one pending approval, **zero bookings**, **zero customer messages sent**.
- Persisted elapsed time: **93.38 seconds**.

The retained draft and pending approval are intentional demo artifacts for later owner review; they are distinct from rolled-back automated test fixtures.

Repeated the same command/request key: exit code 0, identical verified JSON report, same run ID, still one draft/one approval/zero bookings. No new model request or business action was needed by this replay path.

Read the persisted plan and outcome without model inference:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --status 608afe1f-33e9-4ee7-b1c0-4fa9cb225252
```

## Limits and remaining phases

No authenticated owner decision/resume endpoint exists yet. Interrupted running runs require inspection; automatic restart recovery is Phase 7. Reports certify persisted tool evidence and validated plan completion only, not independent semantic fulfillment of an arbitrary business goal. In-flight database transactions finish under their existing timeouts before final reporting rather than being cancelled with uncertain writes. Phase 7 must finish owner authentication/decision lifecycle, reconciliation/retry/resume, additional concurrency coverage and administrative slot-edit guards. Phase 8 adds the dashboard.

Frontend checks were not repeated because its code/dependencies were unchanged. `git diff --check` passed. Secrets remain in the ignored backend environment file. No commits or pushes were performed.
