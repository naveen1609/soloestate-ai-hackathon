# Phase 9 verification — 28 October 2026

Expanded tests for persisted failures, malformed model output, unsupported completion, duplicate actions, rejected consent, stale booking targets and database rollback. Added **11 PostgreSQL regression cases** and **6 frontend cases**. No runtime dependencies, migrations, credentials or production model mocks were added.

## Defect reproduced and corrected

Three new component cases failed before the fix: an empty HTTP 200 acknowledgement, the wrong approval ID, and an opposite decision could dismiss the approval dialog. The dialog now validates the backend's approval_id and decision against the explicit owner action before confirming. Invalid acknowledgements retain the review, error state and original decision key, with the opposite choice disabled. All three regression cases passed after the fix. An unsupported test query option was caught by TypeScript and corrected before the production build passed.

## Executed checks

Commands ran from backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q tests/test_agent_database.py tests/test_integrity_database.py --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_tools_database.py tests/test_dashboard_sessions.py --run-tool-db-tests --run-integrity-db-tests
```

- Default suite: **123 passed, 55 skipped**, one upstream Starlette/AnyIO deprecation warning. No broken requirements.
- Agent/integrity suite: **31 passed** in 333.25 seconds, including all new database cases. All external model turns were mocked; business persistence and transaction/constraint behavior were real PostgreSQL.
- Tool/session suite: **21 passed** in 111.28 seconds, with one upstream warning. Combined with the default suite and excluding the two repeated API cases, **173 distinct backend cases passed**. Five original schema cases remain skipped; this coverage was executed in groups rather than claimed as a single full-suite invocation.

Default tests and the database groups together cover the current backend suite. Two session API cases occur in both the default and session groups and must not be counted twice. The five dedicated-empty-database schema cases remain unexecuted; their required TEST_DATABASE_URL must not point at the application database.

Commands ran from frontend:

```powershell
npm test
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

**38 unit/component tests passed**; lint, typecheck and production build exited successfully. **6 browser tests passed, 1 skipped** in 1.4 minutes. Browser tests used Edge, the production build and real FastAPI/Supabase services. The isolated draft approval was created for the test and deleted by exact UUID. The existing demo approval was previewed without deciding it. No real model requests were made in Phase 9; prior Phase 8 real-model evidence remains in its historical verification document.

Final database inspection confirmed the retained run is awaiting_approval, with one pending approval and zero owner decisions. No run fixtures with the test suite's fixture goals remained. Locally started validation services were stopped after verification.

## Scope and remaining checks

The tests assert persisted counts/IDs/state, actual tool feedback, no model calls after rejection, transaction rollback after consent-report failure, no duplicate business write for distinct native call IDs sharing one business key, and no fabricated sent messages. Existing independent-session races continue to check booking, owner decisions, worker leases and ticket exchange.

This phase adds regression evidence; it does not establish production-scale reliability, independently certify natural-language goal fulfillment, or implement customer delivery. The five separate empty-database checks remain an explicit test limitation, not a hidden success claim. Phase 10 setup/demo packaging is still separate.

See [testing.md](testing.md) for the coverage matrix, repeatable commands, expected results and safe failure-report instructions.
