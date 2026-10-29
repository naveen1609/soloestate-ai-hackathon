# Phase B4 verification

Implemented contract: owner-managed follow-up tasks as internal work tracking, with a validated status lifecycle, versioned content edits that invalidate pending review, CHECK-bound outcomes on terminal done, read-time overdue derivation, per-task history, and truthful non-delivery states. No scheduler, notification sender, channel, receipt, vector feature or autonomous behavior; the agent-run draft/approval flow and seven-tool registry are unchanged. [Approved B4 contract](phase-b-plan.md#b4-contract--approved-29-october-2026), [decisions](DECISIONS.md). Migration `0008_followup_tasks.sql` adds private RLS `followup_tasks`; migrations 0001–0007 and seed records are unchanged.

## Actual checks

### Migration and seed integrity

- `python -m scripts.database migrate`: status ok, applied `["0008_followup_tasks.sql"]` only.
- `python -m scripts.database verify`: status ok, **eight migrations**, original fictional seed IDs **4 leads / 3 properties / 6 visit slots**. No reset or reseed.

### Focused B4 backend suites

- `pytest -q tests/test_followups.py -p no:cacheprovider`: **31 passed, 1 warning (existing Starlette/AnyIO deprecation) in 6.61s, exit code 0.** Covers task schemas, transition-graph pinning with no sent/delivered state, review-invalidation matrix, overdue derivation and owner-required routes. No database touched.
- `pytest -q tests/test_followups_database.py --run-integrity-db-tests -p no:cacheprovider`: **10 passed, 1 warning in 105.70s, exit code 0.** Covers create/detail/listing with due-first ordering, active-property context, versioned edits with explicit-null semantics, the full lifecycle walk with outcome, invalid edges, cancel/reopen, review invalidation, replay/conflict, stale versions, archived-lead blocks, opt-out/handoff independence, history snapshots and HTTP wiring. All database writes rolled back.

### Complete backend regression

- `pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests -p no:cacheprovider`: **414 passed, 5 skipped, 1 warning in 1532.02s (0:25:32), exit code 0.** Ran once, uninterrupted, without --lf. Count reconciles exactly: B3 baseline 360 passed plus 12 grounding/stale-citation correction tests plus 41 new B4 tests plus 1 new migration-syntax parameter (`test_postgresql_sql_syntax` covers the new file). The five skips are the unchanged dedicated-empty-database schema tests; the warning is the existing Starlette/AnyIO deprecation.

### Frontend tests, lint, typecheck, build

- `npm.cmd test -- followup-tasks.test.tsx crm.test.ts`: first run **76 passed, 2 failed in 32.65s** under roughly triple normal load (a 5000ms test-budget timeout on the multi-step create flow and one value mismatch, both in the new component file); the identical command then passed **78/78 three consecutive times** (9.93s, 15.27s, 9.49s, exit code 0), and the component file alone passed 5/5. No code changed between these runs; the first failure is attributed to extreme parallel-worker load, in the same class as the recorded B3 frontend flake.
- `npm.cmd test`: **136 passed across 11 files in 37.10s, exit code 0** (120 baseline plus 16 B4: task panel component, BFF allowlist pins including send/deliver rejection).
- `npm.cmd run lint`, `npm.cmd run typecheck -- --incremental false`: clean, zero errors and warnings. `npm.cmd run build`: passed, exit code 0, with all existing routes intact.

### Browser verification

- `npm.cmd run test:e2e` with the backend on :8000 and production frontend via the Playwright webServer (Edge channel, live-Meta intentionally skipped): **7 passed, 1 skipped, 0 failed in 11.8m, exit code 0.** The CRM workflow covers task create with due date, list ordering, advance through in_progress and in_review, completion with an outcome, alongside all prior B1/B2/B3/booking checks. Read-only probes confirmed zero leftover fixture rows (leads, tasks, conversations, properties) after the run.
- Two earlier runs failed on B4 spec defects, both test-only with no application change: the first (6 passed / 1 skipped / 1 failed in 8.5m) clicked the auto-selected new task button, which is disabled by design as in the conversation panel; the fix removes the redundant click. The second (6 passed / 1 skipped / 1 failed in 10.2m) filled the outcome before the post-transition remount settled, leaving the complete button correctly disabled; the fix waits for the in_review status first. Fixture cleanup was probe-confirmed at zero after both failed runs, which also exercised the new followup_tasks cleanup path.

## Changed files in this B4 increment

Backend: `app/followups.py`, `app/crm_schemas.py` (2 B4 schemas), `app/crm.py` (idempotency field binding for TaskUpdate), `app/crm_api.py` (4 routes), `tests/test_followups.py`, `tests/test_followups_database.py`, `tests/test_database_integration.py` (18→19 tables). Migration: `supabase/migrations/0008_followup_tasks.sql`. Frontend: `components/followup-tasks.tsx` (+test), `components/crm-records.tsx` (lead-detail mount), `lib/crm.ts` (schemas), `app/api/owner/[...path]/route.ts` + `crm.test.ts` (allowlist), `e2e/crm.spec.ts` + `e2e/crm_fixture.py` (B4 workflow and cleanup). Docs: this report, `phase-b-plan.md` (contract), `DECISIONS.md`, `database.md`, `testing.md`, `limitations.md`, `ROADMAP.md`, `PROJECT_STATE.md`, `HANDOFF.md`. Migrations 0001–0007 have no HEAD diff; the seven-tool registry is unchanged; B4 sends nothing to the model.

## Test boundaries and limitations

The five dedicated-empty-database schema cases still need a separate TEST_DATABASE_URL; skips are not passes. The browser live-Meta case stays opt-in. Due dates and overdue flags are reminders as data: no scheduler, escalation or notification exists, and done is terminal internal completion, never delivery. Tasks carry free-text assignees with no user system and ignore B1 opt-out/handoff by design; archived leads block mutations until restored. The lead-detail task panel shows the first 20 records in due-date order without pagination; the list API itself accepts page/page_size. See [limitations](limitations.md).

## Acceptance-defect corrections — 29 October 2026

Independent review identified three B4 acceptance defects. All are fixed and verified below with no migration change.

**Review-invalidation bypass.** `resolve_status('in_review', True, 'in_review')` demoted to in_progress and then re-applied the valid in_progress→in_review edge, so a content edit could retain pending review in a single request. Combined content-plus-review requests are now rejected (`Revise and resubmit for review in separate operations.`); resubmission and completion each need a separate versioned operation. Non-review targets combined with content (in_progress, open, cancelled) still resolve through demote-then-validate. Rejected requests leave task, activity and operation records unchanged.

**Thin history snapshots.** Activity entries carried only task ID, status and outcome, so content edits produced identical before/after snapshots. Snapshots now record the full backend-owned task (ID, lead/property, title, notes, due date, assignee, status, outcome, version) serialized with the shared encoder, persisted atomically in the mutation transaction. Older three-field entries are preserved, never backfilled, and remain readable in both the API and the UI. The task panel renders a per-entry change summary computed defensively from before/after snapshots.

**Creation-path locking.** Task creation read lead/property state without locks while archival (`FOR UPDATE` on leads) and property deactivation (`FOR UPDATE` via save) could commit concurrently. Creation now share-locks both reads, matching the update path's lead lock and serializing against the exclusive writers.

Focused B4 suites after the corrections: **60 passed, 1 warning (existing Starlette/AnyIO deprecation) in 302.28s, exit code 0** (`pytest -B -q -p no:cacheprovider tests/test_followups.py tests/test_followups_database.py --run-integrity-db-tests`; 34 unit + 26 database, including per-field invalidation, combined-request rejection with unchanged-records proof, unchanged-content non-invalidation, snapshot accuracy, revision visibility, replay-activity checks, rollback on audit failure, history isolation, legacy readability, and two independent-session race tests with committed fixtures deleted by exact ID; probe-confirmed zero leftover B4 fixtures). Shared regressions: **93 passed** database-free (`test_crm.py test_journey.py test_faq.py test_conversation.py`, 15.03s, exit 0) and **20 passed** (`test_crm_database.py --run-integrity-db-tests`, 360.74s, exit 0), same warning. No shared application file changed, so the B3 database suites and the 25-minute full suite were not rerun: they exercise untouched code paths and would add no signal beyond these covering runs.

Frontend after the history-rendering change: focused `npm.cmd test -- followup-tasks.test.tsx crm.test.ts` first showed 79 passed / 1 failed in 25.84s on the pre-existing create-flow test under load, then **80/80, exit 0** (15.73s) on the identical tree; full `npm.cmd test` **138 passed across 11 files in 50.94s, exit 0**; lint/typecheck clean; production build passed, exit 0. Sequential-worker closure runs with all other test/build processes finished: `npm.cmd test -- followup-tasks.test.tsx crm.test.ts --maxWorkers=1` gave **80 passed across 3 files in 26.58s, exit 0**, and `npm.cmd test -- --maxWorkers=1` gave **138 passed across 11 files in 61.81s, exit 0**, both first-try green with no timeouts; no assertion, timeout, or application change was needed. Browser `npm.cmd run test:e2e`: **7 passed, 1 skipped, 0 failed in 12.2m, exit code 0**, first-try green, with probe-confirmed zero leftover fixtures. Port 8000 was held by a pre-existing process (SoloEstate uvicorn, PID 5408, started 19:02) whose ownership could not be verified, so it was left untouched and the verification backend ran on :8001 with BACKEND_URL pointed at it; my server was stopped and the port verified free afterward.

Corrected in this session: `backend/app/followups.py`, `backend/tests/test_followups.py`, `backend/tests/test_followups_database.py`, `frontend/src/components/followup-tasks.tsx` (+test), and docs (contract clarification, decisions, testing, limitations, this report, state, handoff). Migrations 0001–0008 unchanged; the seven-tool registry, agent-run drafts, approvals, consent controls and booking integrity untouched.
