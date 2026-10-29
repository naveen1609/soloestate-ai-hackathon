# Phase B2 qualification and matching verification

Date: **29 October 2026**, Asia/Kolkata. Source: **main / caf4ad3** plus the preserved B1/documentation work and this approved B2 increment. No commit, push, deployment or customer contact. This report records actual checks, not expected totals.

## Implemented contract

The owner-approved plan reuses leads/properties and existing approval/booking controls. Migration 0006 permits unknown requirements on the original lead columns, adds nullable purchase_timeframe and a server-managed qualification_fingerprint, and preserves existing records. No parallel CRM, matches table, external delivery, conversation/FAQ module, new mutation tool or background job.

Matching readiness requires budget minimum/maximum, preferred localities and minimum bedrooms. Values are backend validated: finite nonnegative ordered INR bounds (two decimals), nonblank deduplicated localities (1–20), strict integer bedrooms (1–30). A positive ceiling is required for matching/qualification; zero is retained as a recorded value but flagged for review, never invented as an unknown-answer default. Timeframe is within_3_months / three_to_six_months / later / no_fixed_deadline, or NULL when unanswered.

Qualification readiness additionally requires timeframe. Owner POST /owner/crm/leads/{id}/qualify needs expected_version, request_key and a nonblank reason. First confirmation requires the qualifying journey stage; qualified leads can be re-reviewed. The existing generic journey transition into qualified uses the same guard. Matching, lead status, journey, customer permission and booking approval remain separate.

Semantic requirement edits clear the current fingerprint and append actual before/after activity in the same transaction. Returning to old answers does not revive an invalidated review. No historical evidence is rewritten. Contact/handoff/notes changes do not change the requirement fingerprint. Owner review does not lift opt-out or resolve handoff. Archived leads cannot receive requirement/review writes or new matches.

GET /owner/crm/leads/{id}/matches?limit=1..20 and get_property_matches use one shared deterministic service. Active properties must meet inclusive budget, normalized exact locality and minimum bedrooms. Order: exact bedrooms, price, ID. Existing 100/90 scores are ranking rules, not confidence. Results carry lead/property versions, criteria snapshot, check timestamp and requested/actual/passed explanations. Incomplete criteria are distinct from a complete query with no inventory. Timeframe is not a property filter because possession dates are not recorded.

Lead PATCH preserves omitted fields and clears explicit-null requirements. Operation identity distinguishes those requests. A legacy fingerprint fallback permits unchanged pre-B2 lead-edit replay against its original response; operation lookup remains available. No new model write authority; seven existing tools remain allowlisted. Meta receives readiness data and prompt guidance but cannot replace backend validation.

## Actual checks

- Before edits: inspected Git/status/recent commits, AGENTS/state/roadmap/decisions/handoff/full Phase B plan/B1 report, actual CRM schemas/tools/matcher/consent/booking/tests. Captured hashes for 137 existing versionable files; preserved dirty work.
- Database-free backend command: `python -m pytest -q -m "not database and not tools_database and not agent_database and not integrity_database"`: **189 passed, 86 deselected**, 4.69s, existing Starlette/AnyIO warning. Additional B2 database cases were added afterward; this deselection count is the actual intermediate collection, not the final suite total.
- Existing tool/journey plus B2 focused command: `python -m pytest -q tests/test_qualification.py tests/test_qualification_database.py tests/test_tools_database.py tests/test_journey_database.py --run-integrity-db-tests --run-tool-db-tests`: **45 passed**, 195.27s. Rollback-only fictional PostgreSQL fixtures, no inference/delivery.
- Intermediate expanded B2 run: **29 passed**, 111.40s. Two operation-identity compatibility tests were added afterward.
- Final focused command from backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_qualification.py tests/test_qualification_database.py --run-integrity-db-tests
```

**31 passed, 1 warning**, 114.57s. Covers unanswered data, validation, authorization, persistence, effective partial-update bounds, stale/replayed/conflicting writes, both qualification entry paths, atomic audit failure, current-review invalidation, matching boundaries/ties/truncation/explanations/API-tool equivalence, opt-out/handoff preservation and independent-session review/edit contention with exact fictional cleanup.

- `python -m scripts.database migrate`: status ok, applied only **0006_lead_qualification.sql**. Migrations 0001–0005 were not edited; no seed command.
- `python -m scripts.database verify`: status ok, **six migrations**, original fictional seed IDs **4 leads / 3 properties / 6 visit slots**.
- Frontend intermediate `npm.cmd test`: **86 passed across 8 files**, 8.72s. Final lead-ID/version isolation test was then added; next run: **87 passed across 8 files**, 8.88s. Agent-dashboard incomplete-result compatibility was then covered; final run: **88 passed across 8 files**, 9.05s. Earlier baseline run was 73.
- `npm.cmd run lint`, `npm.cmd run typecheck -- --incremental false`, `npm.cmd run build`: passed. Initial new-test lint had two unused-parameter warnings; corrected and rerun clean. Final build includes the responsive panel, lead-ID/version match isolation, agent-dashboard incomplete-result compatibility and browser-test additions.

### Complete backend regression

From backend, using the existing .venv (Python 3.12.9), configured PostgreSQL and verify-full TLS:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
```

**282 passed, 5 skipped, 1 warning in 1008.98s (0:16:48), exit code 0.** Ran once, uninterrupted, without --lf. No backend test failed or needed weakening. Backend source remained unchanged during this full run. Existing CRM, agent/model, consent, approval, audit/rollback, booking and concurrency regressions passed with the B2 checks.

The five skipped tests in test_database_integration.py are migrations/seed repeatability, budget/FK constraints, slot exclusion, booking uniqueness/approval binding and RLS/PUBLIC access. Their fixture requires an absent dedicated-empty TEST_DATABASE_URL; they remain unexecuted, not passes. No application database was repurposed as an empty schema-test database.

One warning: starlette/testclient.py:45, DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead. This upstream dependency warning was not addressed through unrelated upgrades.

Real browser verification is complete; see the browser closure section below.

### Browser verification closure — 29 October 2026

Two earlier runs of the documented browser suite failed identically at `frontend/e2e/crm.spec.ts:59`: `getByLabel('Purchase timeframe', {exact:true}).selectOption('within_3_months')` timed out after 30s even though the control was present, enabled and correct (confirmed in the failure screenshot and Playwright snapshot). Both runs had passed every step through line 58. Cause: exact `getByLabel` matching fails against the edit form's implicit wrapping `<label>` for this `<select>`; every other select in the suite passes because it either omits `exact` or carries an explicit `aria-label`. Approved one-line test-only fix: remove `{exact:true}` from that locator. No application, backend or migration change.

Final run, from frontend with the backend on 127.0.0.1:8000 (Playwright webServer auto-starts the production build; Edge channel; default E2E_BASE_URL; SOLOESTATE_LIVE_UI unset):

```powershell
npm.cmd run test:e2e
```

**7 passed, 1 skipped, 0 failed in 6.9 minutes, exit code 0.** The CRM workflow (4.4 minutes) covers creating a lead with unanswered requirements, incomplete matching, saving partial answers, B1 journey/permission/handoff with reload persistence, lead edit/search, property create/edit, live explained matches, timeframe completion, explicit owner qualification with reload persistence, requirement-edit invalidation, requalification, booking, approved rescheduling, cancellation and archiving. The six dashboard tests cover sign-in lockout, persisted plans/timeline/review, review-snapshot close, narrow viewport, used-link rejection and isolated draft approval. The skip is the deliberate live-Meta opt-in (`runs a real read-only goal through the dashboard`), which requires explicit SOLOESTATE_LIVE_UI=1; no billable model call was made.

Read-only probes around the run confirmed migration 0006 columns present and zero `BrowserCRM-*`/isolated-run fixtures before and after; the suite's `finally` cleanups removed every fixture record. No database reset or reseed. The sandboxed shell could not reach the venv interpreter or loopback ports, so the backend, suite and probes ran with owner-context execution; the commands themselves are the documented ones.

## Changed files in this B2 increment

Compared with the 137-file versionable entry snapshot (which already contained B1 and prior documentation edits). No entry file was removed. Files not listed retain their entry contents; applied migrations 0001–0005 and owner.py remain unchanged.

- `README.md`
- `backend/app/agent/orchestrator.py`
- `backend/app/crm.py`
- `backend/app/crm_api.py`
- `backend/app/crm_schemas.py`
- `backend/app/journey.py`
- `backend/app/qualification.py`
- `backend/app/tools/schemas.py`
- `backend/app/tools/service.py`
- `backend/tests/test_qualification.py`
- `backend/tests/test_qualification_database.py`
- `docs/DECISIONS.md`
- `docs/HANDOFF.md`
- `docs/PROJECT_STATE.md`
- `docs/ROADMAP.md`
- `docs/architecture.md`
- `docs/database.md`
- `docs/limitations.md`
- `docs/phase-b-plan.md`
- `docs/phase-b2-verification.md`
- `docs/testing.md`
- `docs/tools.md`
- `frontend/e2e/crm.spec.ts`
- `frontend/src/app/api/owner/[...path]/crm.test.ts`
- `frontend/src/app/api/owner/[...path]/route.ts`
- `frontend/src/components/crm-records.tsx`
- `frontend/src/components/crm.test.tsx`
- `frontend/src/components/dashboard.test.tsx`
- `frontend/src/components/dashboard.tsx`
- `frontend/src/components/lead-qualification.test.tsx`
- `frontend/src/components/lead-qualification.tsx`
- `frontend/src/lib/crm.ts`
- `supabase/migrations/0006_lead_qualification.sql`

## Test boundaries and repeatable demo

All backend external inference is mocked; database tests use real PostgreSQL. B2 fixtures roll back, except its concurrent review/edit case which briefly commits uniquely named fictional records and removes only those IDs in finally. Browser fixtures create unique fictional lead/property/slot records and clean activities before operations. Never reset or reseed the demo database to run tests. The original five dedicated-empty-database schema cases need TEST_DATABASE_URL separate from the application database; skips are not passes. The browser live-Meta case stays opt-in.

For the owner walkthrough after verification: start backend/frontend using README, generate a private owner login link locally, create a lead with name/email and blank requirements, check incomplete matching, enter budget/localities/bedrooms, inspect explained live matches, enter timeframe, move the journey to qualifying and explicitly confirm with a reason. Reload, edit a requirement, observe needs-review, then reconfirm. Existing reviewed booking/rescheduling/cancellation still use the same records and approval controls. No messages are sent. Keep login URLs, credentials and cookies private.

Limitations: owner-entered answers/review are not independent verification of customer truth or consent. Matching is exact/deterministic, not geospatial, fuzzy, valuation, conversion prediction or availability certification. No possession-date filter, automatic stage advancement, qualification-by-model, delivery, FAQ/conversations, tasks or background work. Internal read-only matching may be used during opt-out/handoff; customer-directed tools remain blocked by B1 controls. Owner booking retains its existing independent approval rules; qualification does not create or approve bookings.
