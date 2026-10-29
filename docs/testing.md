# Regression tests

Current B2 results and the full-suite command are recorded in [phase-b2-verification.md](phase-b2-verification.md). Older counts below describe their historical phase baselines; do not substitute an expected count for actual execution.

External model responses are mocked in automated tests. Business integration tests use real PostgreSQL transactions and fictional records, with outer rollback or exact UUID cleanup for independent-session races. No test approves the retained demo run or sends customer messages. The live model check remains a separate opt-in demonstration.

## Coverage matrix

| Required scenario | Executable evidence |
| --- | --- |
| Normal execution | `test_plan_then_tools_then_verified_finish`, `test_persisted_plan_steps_and_replay_without_new_model_call`, persisted dashboard browser test |
| Malformed model output | `test_malformed_model_plan_persists_failure_without_business_writes`, argument validation and provider contract tests |
| Unavailable appointments | `test_slot_taken_after_approval_is_refused`, `test_disabled_slot_after_approval_is_refused`, `test_elapsed_slot_is_neither_listed_nor_booked`, `test_property_withdrawal_after_consent_leaves_booking_uncommitted` |
| Rejected owner consent | `test_rejected_run_resume_is_idempotent_and_never_calls_model`, `test_rejection_cancels_without_delivery`, explicit rejection component test |
| Duplicate actions | `test_distinct_model_call_ids_replay_one_business_action`, `test_completed_request_key_cannot_change_goal_or_permissions`, independent-session booking replay and owner-decision races |
| Database failure | `test_audit_failure_rolls_back_business_write`, `test_owner_audit_failure_rolls_back_consent_and_draft_status`, commit-failure and rollback-only retry tests, sanitized HTTP 503 tests |
| Truthful reporting | `test_false_completion_cannot_replace_persisted_tool_evidence`, missing-count/failed-state UI tests, malformed decision acknowledgement tests |
| Booking integrity | Independent-session double booking, consumed approval reuse, changed approval snapshots, slot locks and database guard tests |
| Session/security boundary | Expiry/revocation/single-use ticket races, HttpOnly browser cookie, fixed BFF endpoint allowlist, same-origin mutation checks, model permission denial |

Phase 9 adds 11 database regressions and 6 component cases. New malformed-acknowledgement tests reproduced a real UI problem: a 200 response without the requested approval ID and decision could dismiss the review as confirmed. The UI now checks both fields and retains the dialog and original retry key when confirmation is invalid. This does not change consent or booking rules.

## Commands

From backend:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Expected with no TEST_DATABASE_URL configured: **152 passed, 75 skipped**, plus an upstream Starlette/AnyIO deprecation warning; dependency check reports no broken requirements. Default tests do not opt into the configured application database, but a configured TEST_DATABASE_URL enables the five separate schema tests.

For an inspection that must not touch any database, explicitly exclude every database marker:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider -m "not database and not tools_database and not agent_database and not integrity_database"
```

Expected: **152 passed, 75 deselected**. Do not run browser tests, owner login, migrate or seed commands as part of that read-only inspection: those operations can write session or business/setup records.

For all existing and new tool/agent/integrity database cases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
```

Expected: **222 passed, 5 skipped** with configured Supabase access and migrations 0001–0004. Tests never use real model credits. The five original schema cases require a separate empty disposable TEST_DATABASE_URL and are excluded from application-database runs. Never point TEST_DATABASE_URL at the demo or production database; see [database.md](database.md).

To run that full coverage in smaller groups, run the default suite above and all three database groups below. Each database group intentionally writes isolated test records; it is not part of a read-only inspection.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_database.py tests/test_integrity_database.py --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_tools_database.py tests/test_dashboard_sessions.py --run-tool-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_crm_database.py --run-integrity-db-tests
```

Expected group counts: **31**, **21** and **20** passed, respectively. Two session API cases overlap the default suite, so do not add the group counts without accounting for that overlap. The five separate empty-database schema cases remain skipped unless configured independently.

From frontend:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\frontend'
npm test
npm run lint
npm run typecheck
npm run build
```

Expected: **65 frontend tests pass**, lint/typecheck exit zero and production build completes. Stop a running production frontend before rebuilding its output directory.

For browser checks, start the backend as documented in [dashboard.md](dashboard.md), then run:

```powershell
npm run test:e2e
```

Expected: **7 passed, 1 skipped** with installed Microsoft Edge and a configured backend. The suite uses temporary fictional runs rather than a historical demo run. The live-model case is deliberately skipped unless SOLOESTATE_LIVE_UI=1. Browser approval testing uses a newly created fictional draft and deletes only its records afterward. Do not run several browser suites concurrently against the same output directory.

## Failure reports

Share the exact command, failing test name, assertion/traceback and sanitized terminal logs. For browser failures include the matching error-context file or screenshot from frontend/test-results after checking it contains only fictional data. Keep .env contents, sign-in links, cookies, API keys and credential-bearing database URLs private. A connectivity/permission failure is not evidence that a business operation succeeded. For an ambiguous real request inspect its saved run/key before retrying.

No new runtime dependency, migration, secret or Supabase configuration was required for Phase 9. Historical verification records are in [phase-9-verification.md](phase-9-verification.md) and [phase-10-verification.md](phase-10-verification.md). Current CRM and regression evidence is in [phase-a-verification.md](phase-a-verification.md).

Phase 10 adds five preflight unit tests (readiness, missing configuration, sanitized errors, failed database gating and service health validation). These do not call Meta or PostgreSQL. See phase-10-verification.md for executed evidence.

Browser isolation fix (28 October 2026): approval changes made during manual dashboard use must not invalidate browser assertions. Stateful browser checks now create their own uniquely identified fictional lead/property/run, execute real read/draft/approval tools with persisted evidence, and clean up in fixture teardown. They no longer assume a particular historical run is pending or has a fixed model timeline. No real model credits are used unless the live case is explicitly enabled.

The isolated browser suite was executed after this fix: **6 passed, 1 skipped** in 3.2 minutes. Lint and TypeScript checks passed. The skipped case is the explicit live-Meta opt-in. Existing application/runtime code was unchanged.

## Phase A CRM coverage

Current CRM/appointment tests live in backend/tests/test_crm.py, backend/tests/test_crm_database.py, frontend/src/components/crm.test.tsx and frontend/e2e/crm.spec.ts. They cover validated owner CRUD, query filters/pagination, missing/stale records, archive/deactivation, versioned consent/booking, cancellation, atomic replacement, operation replay, commit/report failure and competing bookings. See phase-a-verification.md for current executed counts; older counts above describe earlier increments. The ordinary browser suite now has one additional real CRM owner journey.

## B1 coverage and commands

New checks: `test_journey.py`, `test_journey_database.py`, `test_contact_database.py`, `test_contact_crm_database.py`; frontend `lead-journey.test.tsx`, the CRM immediate-edit regression, BFF allowlist cases and the real CRM browser workflow. Journey/contact DB fixtures roll back; the browser workflow removes only its uniquely identified fictional rows, including lead_activities before CRM operations. No external model or delivery call is enabled.

From backend:
```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_journey.py tests/test_journey_database.py tests/test_contact_database.py tests/test_contact_crm_database.py --run-integrity-db-tests --run-tool-db-tests
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
```

From frontend: `npm.cmd test`, `npm.cmd run lint`, `npm.cmd run typecheck -- --incremental false`, `npm.cmd run build`; with backend on 8000, `npm.cmd run test:e2e`. Do not rebuild a running production frontend. Current executed results and initial failed attempts are in [B1 verification](phase-b1-verification.md), summarized in [HANDOFF](HANDOFF.md); earlier numbers above remain historical. Five dedicated-empty-database tests remain opt-in and must never target the demo database.

## B2 coverage and commands

New backend files: test_qualification.py and test_qualification_database.py. They cover unknown/invalid answers, readiness versus explicit review, authorization, persisted nulls, effective merged-bound validation, fingerprints/audit rollback, stale/conflicting requests, null-versus-omission idempotency, pre-B2 replay, generic journey guards, matching boundaries/ties/truncation/explanations/API-tool equivalence, opt-out/handoff preservation and a concurrent owner review/edit race. That race commits only unique fictional IDs, then cleans activities/operations/lead; all other new database fixtures roll back.

From backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_qualification.py tests/test_qualification_database.py --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
```

Apply additive migration 0007 first on a new checkout/database; never reset or reseed existing data to test. Do not run independent database suites concurrently: failure-injection fixtures take transactional table locks. Run the browser suite after backend mutation suites finish.

Frontend: lead-qualification.test.tsx, existing CRM/BFF regressions and the expanded real CRM browser workflow. Run npm.cmd test, lint, typecheck, build, then test:e2e with the configured backend. Production frontend must be stopped before rebuilding. Fixtures remain fictional and exact cleanup includes lead_activities. Final pass/skip/warning/duration evidence is in [phase-b2-verification.md](phase-b2-verification.md); earlier counts above are historical. No live model or external delivery is enabled by these commands.
