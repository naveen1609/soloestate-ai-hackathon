# Phase A verification

## Implementation

- New owner-only CRM schemas, service and routes: app/crm_schemas.py, app/crm.py, app/crm_api.py.
- Additive 0004_core_crm.sql applied successfully. Original migrations and seed were not edited. Thirteen private RLS-enabled tables; bookings and visit_slots remain the appointment model.
- Live operational overview at /; preserved Muse Spark execution at /agent; paginated lead/property/appointment lists, details, search/filter/sort, create/edit, archive/restore and deactivate.
- Owner appointment preparation, explicit review/rejection, booking, controlled future slots, versioned cancellation and approved atomic replacement with immutable linkage.
- Stable request fingerprints, transactionally persisted operation results, stale-version checks, safe retry after unknown responses and reload, and owner provenance without invented AI steps.
- Existing registry and consent guards remain authoritative. Archived leads cannot receive new matches, drafts, booking requests or follow-up approvals. Historical identifiers, snapshots and execution evidence remain preserved.

## Verification increments

Initial backend increment: 129 existing/parser tests passed (55 skipped), then migration 0004 applied. New CRM checks initially produced 39 passes and one lock-contention failure; controlled contention handling was added and the isolated concurrent-booking case passed. The first full regression suite then passed **219 tests, 5 skipped** before three additional archive/missing-write cases were added.

Frontend increments: the existing **38 tests** passed, then **63** with CRM/proxy coverage, then **65** including reload recovery. Lint, TypeScript and production builds passed. An unsupported mock call type was caught by TypeScript and corrected. No external inference responses were added to runtime code.

A first browser attempt exposed a select-control locator issue; select locators were changed to accessible combobox roles and bounded timeouts. The real end-to-end CRM journey subsequently **passed in 3.0 minutes**, creating/editing/searching fictional records, creating slots, reviewing consent, booking, rescheduling, cancelling, deactivating and archiving through the UI. These were real HTTP/PostgreSQL operations, not mocked success callbacks.

A combined regression run exposed the overly broad CRM appointment advisory lock: isolated transaction fixtures could block a browser operation on an unrelated slot. The UI correctly retained its request for retry, but the lock was narrowed to workflow/booking rows and the shared slot locks. Same-slot conflicts remain protected by existing consent checks, row locking and PostgreSQL uniqueness/exclusion constraints. Final results are recorded below after those reruns.

## Execution commands

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.database verify
```

Frontend:

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
npm.cmd run test:e2e
```

The owner's existing service on 8000 was preserved. Verification used an updated API on 8001 and production Next.js on 3001 with server-only BACKEND_URL. E2E_BASE_URL selected that test origin. Default commands still use 8000/3000.

No live Meta credits were consumed in Phase A. Existing model transport/orchestration tests were included in regression coverage. The live-Meta browser case remains opt-in. Five dedicated empty-database schema cases remain explicitly skipped; they require a separate disposable TEST_DATABASE_URL.

## Manual use and scope

Restart the normal servers after applying the updated code; a running old process can continue serving old routes. Follow phase-a.md for exact startup and owner walkthrough instructions. No new secret, external integration or account setup is required. No commit, push or deployment was performed.

Remaining intentional limits: confirmed/cancelled appointment statuses, latest-20 related-record summaries and owner-request overview, single-owner sessions, no delivery/calendar/chat integration or background scheduler. Searchable main entity lists are paginated. The separate customer-journey proposal is phase-b-plan.md.

## Final results

The complete suite was rerun after scoped appointment locking was installed: **222 passed, 5 skipped** in **799.37 seconds**, with one upstream Starlette/AnyIO deprecation warning. No broken requirements were found. Database verification confirmed **4 migrations** and the unchanged seed-ID counts **4 leads / 3 properties / 6 slots**.

Final frontend checks: **65 unit/component tests passed**, lint and TypeScript checks passed, and the production build completed with all CRM and agent routes. Final real-service browser suite: **7 passed, 1 skipped** in **5.8 minutes**. The skipped case is the explicit live-Meta opt-in. The real CRM journey passed with scoped locking, and all six preserved agent/session checks passed in the same run.

Desktop/mobile CRM overview and filtered lead-list screenshots were captured locally and visually inspected. Both 390px layouts were checked for document-level horizontal overflow. Screenshots are ignored artifacts, not published customer data. Runtime endpoints and owner actions in the browser suite used real PostgreSQL records and were not mocked.

Default backend suite was also executed after resumption: **152 passed, 75 skipped** in 4.64 seconds. Default skips include the opt-in business database cases; the full database run above executed those successfully. No source changes were required after the interruption; remaining work was collecting completed verification output and finalizing documentation.

## Final preservation and cleanup

Final read-only inspection found **0 Phase A test runs and 0 Phase A test leads remaining**. The original demo remained pending continuation with **1 approved approval**, preserving the owner's prior decision. All **13** private tables had RLS enabled. Existing environment files, the starting diff backup and generated screenshots remain ignored by Git. All local documentation links resolved, and git diff --check found no whitespace errors.

## Changed implementation files

New backend: `app/crm.py`, `app/crm_api.py`, `app/crm_schemas.py`; new additive migration: `supabase/migrations/0004_core_crm.sql`.

Updated backend integration: `app/main.py`, `app/owner_api.py`, `app/owner.py`, `app/agent/store.py`, `app/tools/service.py`, `app/tools/schemas.py`. The Meta client and original applied migrations were not edited.

New frontend: `src/lib/crm.ts`, `src/components/crm-shell.tsx`, `crm-overview.tsx`, `crm-records.tsx`, `crm-appointments.tsx`, `src/app/crm.css`; routes for agent, leads, properties and appointments. Updated `src/app/page.tsx`, `layout.tsx`, the fixed owner BFF proxy and the existing dashboard's return navigation.

Tests: new `backend/tests/test_crm.py`, `test_crm_database.py`, `frontend/src/components/crm.test.tsx`, BFF `crm.test.ts`, `frontend/e2e/crm.spec.ts`, `crm_fixture.py`; updated schema-table count, execution browser route/origin helpers and Playwright configuration. Existing pre-Phase-A changes were preserved; a local starting diff was saved at `.install-tmp/phase-a-start.patch`.

Documentation: README, architecture/database/dashboard/demo/testing references, this verification record, `phase-a.md` and the separate proposed `phase-b-plan.md`. Historical evidence files were not rewritten to imply new live model execution.
