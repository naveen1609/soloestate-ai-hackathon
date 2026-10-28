# Phase 8 verification — 28 October 2026

Implemented the responsive Next.js owner workspace: goal submission, stable request-key retry/lookup, recent runs, persisted plan preview, backend-confirmed timeline, lead/property results, provisional slots, full approval snapshots, explicit hash-bound decisions, continuation/recovery, and verified outcome reports. No mock model integration or simulated execution results were introduced.

Browser access uses a two-minute single-use local sign-in link, an eight-hour revocable opaque HttpOnly cookie, hashed session storage and same-origin mutation checks. Next.js exposes only fixed owner route/method combinations. Permanent credentials remain backend-only. Migration 0003 adds the twelfth private RLS-enabled table; previously applied migrations are unchanged.

## Automated checks

Commands from frontend:

```powershell
npm test
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

Unit/component checks: **32 passed**. Lint, typecheck and production build exited successfully. An initial browser selector matched Next.js's empty accessibility announcer as well as the intended error; it was narrowed to the meaningful alert. An initial sandboxed production build hit a filesystem permission error; the build passed with permitted output access after stopping the running frontend.

Final browser suite: **6 passed, 1 skipped** in 1.4 minutes. The skipped opt-in real-model case was executed separately and passed. Browser coverage uses installed Microsoft Edge and the real local FastAPI/Supabase services: locked data, single-use sign-in, HttpOnly cookie, persisted plan/timeline/results, review without deciding, mobile layout without horizontal overflow, explicit isolated draft approval and sign-out. The approval fixture is deleted by exact UUID after the test. The retained owner approval is never decided by tests.

Backend command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.database verify
```

Backend suite: **162 passed, 5 skipped** in 363.18 seconds, with one upstream Starlette/AnyIO deprecation warning. Dependency check: no broken requirements. Database verify: **status ok, 3 migrations**, fictional seed counts **4 leads, 3 properties, 6 slots**. Five original schema tests require a separate empty TEST_DATABASE_URL and remain opt-in; they are not claimed as executed. Session checks include hashed storage, single use, expiry, revocation and competing independent ticket exchanges.

## Real model and business evidence

The opt-in read-only browser test was executed separately with SOLOESTATE_LIVE_UI=1 and **passed**. Retained run `419b8264-052f-4269-ae85-3bb0566959fc` is completed in PostgreSQL, with **3 real muse-spark-1.3 responses**, one successful get_leads step returning three fictional leads, zero drafts, zero approvals, zero bookings and zero messages. No API credentials or sign-in links are included in this evidence.

After UI approval testing, the isolated fixture run count was **0**. Original run `608afe1f-33e9-4ee7-b1c0-4fa9cb225252` remained **awaiting_approval**, with **1 pending approval and 0 owner decisions**. Desktop 1440px and mobile 390px screenshots were captured and visually inspected; generated artifacts are ignored by Git.

## Limits

This is the single-owner local hackathon dashboard. There is no message delivery integration, multi-owner identity management, realtime websocket stream or independent semantic certification of goal fulfillment. Draft approval does not send a message. Booking requires subsequent backend execution and a fresh transactional availability check. Sign-out does not cancel accepted execution. Existing retry/lease budgets apply. Phase 9 and Phase 10 remain separate increments.

Setup, exact commands, expected behavior, demo steps and safe failure-report instructions are in [dashboard.md](dashboard.md).
