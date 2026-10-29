# Phase B5 verification

## Independent acceptance and closure — Codex, 30 October 2026

**PASS — B5 acceptance criteria met; ready to close.** The owner subsequently authorized closure and a local checkpoint containing the reviewed implementation, tests, migration and preserved documentation. This checkpoint is based on main/fc7d4a7; resolve its resulting hash through Git. Phase C planning may follow; implementation, external integrations, push and deployment are not authorized by this closure.

| Evidence | Actual result and provenance |
| --- | --- |
| Independent database-free B5/CRM/B3/B4 regression | From backend: `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_visits.py tests/test_crm.py tests/test_conversation.py tests/test_followups.py` — **119 passed, 1 existing Starlette/AnyIO deprecation warning in 5.42s**, exit 0. |
| Independent focused frontend | From frontend: `npm.cmd test -- crm.test.tsx crm.test.ts --maxWorkers=1` — **84 passed across two files in 13.46s**, exit 0. |
| Independent read-only PostgreSQL probe | Explicit `SET TRANSACTION READ ONLY`; **9 migration rows, 4 B5 booking columns, 0 matching B5/browser fixture leads, 0 cancelled bookings with an outcome**. Printed counts only; no database writes or model calls. |
| Independent preservation checks | Expected main/fc7d4a7 baseline; B5 uncommitted and nothing staged at review; migrations 0001–0008, B3 grounding, B4 safeguards, registry and owner approval service unchanged; `git diff --check` passed. |
| Muse's broader execution evidence | Focused backend **47 passed**; full backend **481 passed / 5 skipped / 1 warning**; full frontend **149 passed**, lint/typecheck/build passed; browser **7 passed / 1 intentional live-Meta skip**. These are Muse's reported executions detailed below, inspected by Codex but not independently rerun. |

Review traced API authorization, validation, booking locks, replay/version checks, terminal outcomes, cancelled rejection, replacement lineage, fresh no-show rebooking, follow-up transitions, atomic snapshots and UI/BFF behavior. No blocking defect found. Outcome-versus-cancellation has an independent-session test; outcome-versus-replacement uses the same booking lock and has sequential commit-after-outcome regression coverage, but no dedicated concurrent replacement test. Cancellation itself retains its existing cancellation reason and CRM operation evidence without a lead-activity entry; the new B5 snapshot guarantee applies to outcome/follow-up mutations. Owner-entered outcomes do not independently verify attendance; lifecycle immutability is API-enforced. Five dedicated-empty-database schema cases and live-Meta browser coverage remain unexecuted/opt-in, not passes.

The closure/checkpoint session changes documentation only on top of the accepted application tree. No new tests, migration application, seed, database mutation or application changes were performed solely to create the checkpoint. B5 closure does not certify future automation, channels, automatic journey advancement or Phase C implementation.

Owner-authorized bounded implementation of [the B5 contract](phase-b-plan.md#b5-contract--proposed-29-october-2026) (Visit Proposal to Completion) on **main / fc7d4a7**, 29 October 2026. B5 adds owner-recorded visit outcomes, visit follow-up state and their history to the existing appointment machinery; later milestones remain unstarted/unapproved.

## Actual checks

### Migration and seed integrity

- `python -m scripts.database migrate` applied exactly `["0009_visit_outcomes.sql"]`, exit code 0. Migrations 0001–0008 untouched; the checksum ledger accepts the additive file.
- `tests/test_database.py`: **27 passed in 0.70s** (26 baseline plus the new-file `test_postgresql_sql_syntax` parameter), exit code 0.
- Post-run probe: 9 rows in `schema_migrations` including 0009; zero `BrowserCRM-` and zero `B5-fictional-` fixture leads remain.

### Focused backend suites

- `pytest -q tests/test_visits.py tests/test_visits_database.py --run-integrity-db-tests`: **47 passed, 1 warning in 569.72s (9:29), exit code 0**, first-try green. The warning is the existing Starlette/AnyIO deprecation. Unit coverage pins the five-state derivation, the follow-up graph, partial-update schemas, owner authorization and pre-database rejection of invalid outcome filters. Database coverage pins terminal completed/no-show recording on confirmed bookings, cancelled-row rejection, cancel/replace guards (including a commit-after-outcome rollback), the full follow-up graph with note rules and reopen, exact replay with changed-payload conflicts, stale/missing/archived cases, legacy NULL rows with no implied attendance, exact before/after visit snapshots with audit-failure rollback, a three-booking replacement chain with current-appointment derivation, a fully cancelled thread with no current confirmed appointment, no-show fresh rebooking without history rewrite, the outcome list filter, HTTP wiring, and an independent-session outcome-versus-cancel race with atomic rejection and exact-ID cleanup.
- `pytest -q tests/test_visits.py tests/test_crm.py` (database-free): **48 passed, 1 existing warning in 3.22s, exit code 0.** One initial test-authoring failure (FastAPI resolves the service dependency before query validation) was corrected test-only by asserting on a database factory that must never be called.

### Frontend

- Focused `npm.cmd test -- crm.test.tsx crm.test.ts --maxWorkers=1`: first run failed to compile on an authoring defect (a `record` identifier redeclared inside the new outcome component); renamed the component function with no test change and reran **84 passed across 2 files in 11.76s, exit 0**.
- Full `npm.cmd test -- --maxWorkers=1`: **149 passed across 11 files in 32.45s, exit code 0** (138 baseline plus 4 appointment UI cases plus 7 BFF allowlist cases).
- `npm.cmd run lint`: clean, exit 0. `npm.cmd run typecheck -- --incremental false`: clean, exit 0, after a test-only mock-record type annotation. `npm.cmd run build`: passed with all routes intact, exit 0.

### Browser

- `npm.cmd run test:e2e` with the backend on :8000 and the production frontend via the Playwright webServer (Edge channel, live-Meta intentionally skipped): **7 passed, 1 skipped, 0 failed in 10.3m, exit code 0**, first-try green. The extended CRM workflow covers no-show recording with reschedule-link removal, a 409 API proof that cancel-after-outcome is rejected, fresh rebooking with a NULL replacement link and intact no-show history, reschedule lineage (`rescheduled` with `has_replacement` on the original, `scheduled` on the replacement), completion with a pending next action, the outcome list filter, lead-activity history inspection, and retained cancellation coverage on a fourth booking. Cleanup removed all fixture rows; the probe above confirms zero leftovers.

### Full backend regression

- `pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests`: **481 passed, 5 skipped, 1 warning in 1994.98s (33:14), exit code 0**, ran once, uninterrupted, without --lf. Count reconciles exactly: B4 full-suite 414 passed plus 19 B4 correction tests (previously focused-only) plus 47 new B5 tests plus 1 new migration-syntax parameter. The five skips are the unchanged dedicated-empty-database schema tests; the warning is the existing Starlette/AnyIO deprecation.

## Coverage map

Every B5 acceptance criterion has at least one source test included in the owner-authorized checkpoint: outcome-once/terminal (`test_no_show_is_terminal_and_leaves_history_intact`), cancelled rejection (`test_cancelled_bookings_reject_outcome_writes`), five-state derivation with lineage and current appointment (`test_visit_result_derives_all_five_states`, `test_replacement_chain_derives_lineage_and_current`, `test_fully_cancelled_thread_has_no_current_confirmed_appointment`), cancel/replace guards with fresh rebook (`test_recorded_outcomes_block_cancel_and_replace`, `test_commit_after_outcome_leaves_original_intact`, `test_no_show_rebooks_as_fresh_request_without_rewriting_history`), follow-up graph (`test_followup_graph_with_note_rules_and_reopen`, `test_followup_note_edits_and_invalid_combinations`, `test_followup_works_on_cancelled_visits`), replay/conflict/stale/unauthorized/archived handling (`test_replay_returns_identical_outcome_without_new_writes`, `test_changed_payload_key_reuse_rejected_without_side_effects`, `test_stale_version_rejected_without_side_effects`, `test_outcome_route_requires_owner`, `test_archived_lead_blocks_outcome_writes`), race serialization (`test_outcome_and_cancel_race_serializes_with_atomic_rejection`), snapshots with failure atomicity (`test_history_snapshots_record_visit_before_and_after`, `test_activity_write_failure_rolls_back_outcome_and_operation`), legacy rows (`test_legacy_bookings_keep_null_outcome_with_no_implied_attendance`), and UI/BFF/browser flows above. No test was weakened; no global timeout was raised.

## Changed behavior and files

Migration `0009_visit_outcomes.sql` (four columns plus shape CHECKs on `bookings`); `backend/app/visits.py` (derivation, graph, snapshots, `record_outcome`); `backend/app/crm_schemas.py` (`OutcomeUpdate`); `backend/app/crm.py` (provided-fields replay binding, outcome guard in the shared booking lock path, outcome list filter, derived `visit_result`/`has_replacement` on every appointment read); `backend/app/crm_api.py` (outcome route plus list filter); `backend/tests/test_visits.py`, `backend/tests/test_visits_database.py`; `frontend/src/components/crm-records.tsx` (outcome/follow-up section, derived-result list column, outcome filter options, reschedule/cancel gating, fresh-rebook link); `frontend/src/components/crm.test.tsx` (4 UI cases); BFF allowlist plus `crm.test.ts` (outcome path and query pins); `frontend/e2e/crm.spec.ts` plus a fourth fixture slot in `crm_fixture.py`. Docs: this report, B5 sections in `DECISIONS.md`, `database.md`, `testing.md`, `limitations.md`, and state/roadmap/handoff updates. The seven-tool registry, agent-run draft/approval flow, B3 quotation/citation locks, B4 review/history safeguards, consent controls and journey stages are unchanged; no model, scheduler, notification, channel or delivery surface was added.

## Limitations

Outcomes are owner-asserted facts with no independent attendance verification; terminal means no API path alters a recorded outcome. History covers outcome/follow-up mutations; cancellation itself still writes no activity entry (existing behavior, unchanged). The outcome list filter accepts completed/no-show only. Five dedicated-empty-database schema cases remain unexecuted (absent TEST_DATABASE_URL); the browser live-Meta case stays opt-in. See [limitations](limitations.md).
