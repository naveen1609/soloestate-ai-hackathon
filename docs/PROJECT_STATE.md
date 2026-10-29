# SoloEstate AI — project state

Reviewed: **29 October 2026**, Asia/Kolkata. Source baseline: **main / ea0ea18** (`Complete B3 FAQ and conversation foundation with grounding safeguards`). This is the capability/status index; [HANDOFF.md](HANDOFF.md) holds the changing work queue and Git snapshot. Reinspect both Git and source before editing.

Checkpoint update: the owner approved a local B3 checkpoint commit after independent frontend closure (72 focused tests passed). The checkpoint containing this note is based on a2521ae and includes the preserved B3 source, tests, additive migration and documentation. At that checkpoint B4 was ready for scoped planning only, with implementation, push and deployment remaining separately authorized actions; B4 implementation was authorized and completed afterward (see below). See the final B3 report section and handoff for evidence boundaries.

## Vision and delivery status

SoloEstate AI is an **AI Business Operating System for a one-person real estate company**. The owner directs goals; Meta Muse Spark proposes validated plans and selects permitted tools; the backend executes real operations, observes results and reports persisted evidence. The intended longer-term journey includes contacts, qualification, properties, FAQs/conversations, tasks, follow-ups, visits, channel communication and policy-controlled specialized agents.

- **Implemented:** initial phases 1–10 and **Phase A Core CRM and Appointment Management**, plus B1 journey/contact, the approved B2 qualification/matching increment, the approved B3 FAQ/conversation increment and the approved B4 follow-up/task increment (all verified end to end on 29 October 2026; B4 is verified but uncommitted).
- **Not implemented:** Phase B beyond B4, external delivery, background scheduling and an autonomous agent network.
- **B1:** owner-authorized journey/contact controls on existing leads; see [contract](phase-b-plan.md#b1-contract). **B2:** owner-approved qualification/matching implementation, verified end to end including browser (7 passed / 1 opt-in skip); see [B2 report](phase-b2-verification.md). **B3:** owner-approved FAQ/conversation implementation, verified end to end including browser (7 passed / 1 opt-in skip); see [B3 report](phase-b3-verification.md). **B4:** owner-approved follow-up/task implementation, verified end to end including browser (7 passed / 1 opt-in skip, 11.8m); see [B4 report](phase-b4-verification.md). No later phase is authorized.

## Canonical documentation — reuse, do not duplicate

| Subject | Current source of detail |
| --- | --- |
| Setup, environment assumptions and commands | [README](../README.md), [Phase A walkthrough/API](phase-a.md) |
| Architecture, request/agent sequence and security boundaries | [architecture.md](architecture.md) |
| Tables, migrations, PostgreSQL/TLS and privacy | [database.md](database.md) |
| Executed Phase A verification | [phase-a-verification.md](phase-a-verification.md) |
| B1 verification | [phase-b1-verification.md](phase-b1-verification.md) |
| B2 verification | [phase-b2-verification.md](phase-b2-verification.md) |
| Current B3 verification | [phase-b3-verification.md](phase-b3-verification.md) |
| Current B4 verification | [phase-b4-verification.md](phase-b4-verification.md) |
| Test commands, side effects and coverage | [testing.md](testing.md) |
| Meta integration and data sent to the provider | [model-usage.md](model-usage.md) |
| Seven tool contracts | [tools.md](tools.md) |
| Owner consent, leases, recovery and CLI controls | [owner-recovery.md](owner-recovery.md), [agent.md](agent.md) |
| Known limitations and demo | [limitations.md](limitations.md), [demo.md](demo.md) |
| B1 contract and broader proposed journey | [phase-b-plan.md](phase-b-plan.md) |

Historical phase verification files retain their original results. If a count or behavior differs, inspect source and the current Phase A report; do not rewrite older evidence to imply a new test run.

## Actual implementation map

| Layer | Implemented behavior and files |
| --- | --- |
| Frontend | Next.js/TypeScript/React. `/` live business overview; `/leads`, `/properties`, `/appointments` with detail/create flows; `/agent` preserved AI execution. See `frontend/src/app/` and `src/components/crm-*.tsx`, `dashboard.tsx`. `/leads/new` and `/properties/new` are handled by their dynamic `[id]` route and the component's `new` branch. |
| Browser boundary | Same-origin `/api/session`, `/api/health`, `/api/owner/[...path]`. Explicit method/path/query allowlists in the owner route; HttpOnly owner cookie; no database or model credentials in browser code. |
| API | FastAPI/Pydantic. `backend/app/main.py` includes `owner_api.py`, `dashboard_api.py`, `crm_api.py`. `/owner/crm` provides entity lists/details/writes, slot creation, reviewed appointment commit/reject/cancel, summary and operation lookup; exact methods are in [Phase A API table](phase-a.md#api-and-persistence). |
| Existing run/consent API | `/owner/runs` GET/POST, `/owner/runs/{id}` GET, `/owner/runs/{id}/resume` POST; `/owner/approvals` and `/{id}` GET, `/{id}/decision` POST; `/owner/dashboard/tickets` POST. These require owner authorization. `/dashboard/session` POST/GET/DELETE exchanges, verifies or revokes a session. `/health` is liveness only. |
| Owner CRM | `backend/app/crm.py`, `crm_schemas.py`. Persistent CRUD, archive/restore/deactivate, bounded pagination and fixed sorting, versioned edits, request-key fingerprint/replay and operation audit. No hard deletion of referenced business history. |
| Agent/model | `backend/app/agent/{schemas,orchestrator,store}.py` and `services/meta_model.py`. Real Meta endpoint `https://api.meta.ai/v1` through the OpenAI-compatible SDK, demonstrated with `muse-spark-1.3`; no production mock fallback. Owner policies, registered tools and database facts constrain execution. |
| Registry | `backend/app/tools/{service,schemas}.py`: `get_leads`, `get_property_matches`, `get_available_slots`, `create_followup_draft`, `request_approval`, `book_site_visit`, `get_run_status`. No arbitrary SQL/shell execution, owner-consent tool or external message sender. |

## Database and operation invariants

There are **eight immutable migrations**: `0001_initial.sql`, `0002_execution_integrity.sql`, `0003_dashboard_sessions.sql`, `0004_core_crm.sql`, `0005_customer_journey.sql`, `0006_lead_qualification.sql`, `0007_conversation_foundation.sql`, `0008_followup_tasks.sql`. The runner in `backend/app/migrations.py` also creates the setup ledgers. The **19 private RLS-enabled tables** are listed with their roles in [database.md](database.md#tables-and-invariants); `crm_operations` is the Phase A audit/idempotency addition. Existing `bookings` and `visit_slots` are the appointment model; there is no duplicate appointments table.

- Browser access is through the backend. SQL values are parameterized and handlers/identifiers are fixed. The current local database role is trusted; public deployment needs restricted privileges and production identity/HTTPS work.
- Sign-in tickets are hashed, single-use and expire after two minutes; opaque sessions expire after eight hours and can be revoked. Issuing a login link writes a session ticket and is not a read-only inspection action.
- Customer messages remain drafts. Consent binds immutable recipient/content/version or property/price/time snapshots. Approving a draft does not deliver anything.
- Booking rechecks current data and availability under row locks and database constraints. Rescheduling creates a newly approved linked booking and cancels the original in one transaction; it never edits a confirmed slot in place. Failure leaves the original intact.
- Owner CRM actions have `origin=owner`, no model trace and no invented AI steps. They are not resumable AI runs. Archived leads are excluded from new matching/drafting/booking and cannot receive new follow-up approval.
- Successful responses follow transaction commit. Unknown outcomes retain stable request keys. Stale edits require reloading. Agent retries/leases/resume budgets remain bounded; do not bypass them to force a demo.

## Evidence and limits

The authoritative executed Phase A record is [phase-a-verification.md](phase-a-verification.md#final-results): **222 passed / 5 skipped** backend, **65 passed** frontend, **7 passed / 1 skipped** browser; lint/typecheck/production build passed. Those are recorded completed runs, not checks performed by opening this document. The five schema tests need a separate empty disposable database; the browser skip is live-Meta opt-in. Later database-free inspection results and exact commands are recorded in [HANDOFF.md](HANDOFF.md#verification-ledger).

Current B1 backend verification (**29 October 2026**): the uninterrupted full regression command completed with **250 passed, 5 skipped, 1 warning**, exit code **0**, in **820.28s (13:40)**. The five skips require an absent dedicated-empty TEST_DATABASE_URL; the warning is the existing Starlette/AnyIO deprecation. No application/test/frontend code changed for this check; frontend evidence remains the earlier executed B1 result. See [B1 verification closure](phase-b1-verification.md#full-backend-regression-closure--29-october-2026). The full-backend gap is closed; B1 is ready for a separately authorized B2 increment. B2 remains unstarted.

The fictional seed uses Bengaluru/INR and the fixed **2–8 November 2026** window, with four seed leads, three properties and six slots. Present-time availability can change or expire. Current live database contents and migration application must be checked explicitly when authorized; repository SQL and historical evidence alone do not establish current remote state.

Known limits remain single owner, confirmed/cancelled visits, bounded related/history views, request-bound execution, no external delivery/calendar/chat or background scheduler, and no independent semantic certification of an owner's entire goal. The agent polls every eight seconds; the CRM overview refreshes every fifteen seconds. See [limitations.md](limitations.md) before proposing deployment or autonomy.

## Environment and authorization

Windows/PowerShell workspace: `D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon`; Python 3.12 and Node.js 24/npm, pinned dependencies and an existing local `.venv`/`node_modules`. Backend settings load `backend/.env`; the root example is not loaded. `frontend/.env.local` is optional when the server-only BACKEND_URL default `http://127.0.0.1:8000` is correct. No Supabase browser/API key is required.

Reuse [startup commands](../README.md#run-locally) and [owner walkthrough](phase-a.md#owner-walkthrough). Do not rerun credential initialization, migrations or seed as routine inspection. Full database/browser tests write isolated fictional records; default pytest can run schema tests if TEST_DATABASE_URL is set. For guaranteed database-free checks use [testing.md](testing.md#commands).

Never record secret values, DSNs, session cookies or login fragments here. No external customer contact/delivery, channel activation, provider setup, deployment or publication is authorized by the future product vision. Obtain explicit owner authorization for the specific external action and integration. Only one coding agent may edit this repository at a time; see [AGENTS.md](../AGENTS.md).

B1 adds `backend/app/journey.py`, owner POST lead journey / GET lead activities APIs, `lead-journey.tsx` on the existing detail route, and opt-out/handoff checks in registered tools and owner decisions. New private table: `lead_activities`. State transitions, preferences, permission and human handoff stay independent of lead/booking status. See [decisions](DECISIONS.md) and [roadmap](ROADMAP.md). Current executed results are recorded in HANDOFF; historical Phase A results remain unchanged.

B2 reuses canonical lead requirements with genuine NULL answers, adds purchase_timeframe/current-review fingerprint, derives matching/qualification readiness and logs requirement/review evidence to existing tables. Owner POST lead qualify and GET lead matches routes use fixed authenticated BFF paths; the existing get_property_matches tool shares the same matcher. No new model mutation authority. Matching snapshots carry versions, criteria, time and requested/actual checks; no delivery or availability certification is implied. Final B2 executed evidence, including the browser run (7 passed / 1 skipped, 6.9m, exit 0), is in its scoped report; B1 results remain historical.

B3 adds versioned FAQs (`faq.py`), lead-linked simulated conversations (`conversation.py`), 11 owner API routes, an FAQ manager and lead-detail conversation UI, and regression coverage across backend, frontend and browser. Answers cite pinned immutable FAQ versions or persist as unsupported; handoff/review gates drafting; the seven-tool registry is unchanged and nothing is delivered. Final B3 executed evidence, including the browser run (7 passed / 1 skipped, 9.0m, exit 0), is in its scoped report; B1/B2 results remain historical. A stale-citation correction revalidates cited-version freshness at commit time under row locks (sources revised or archived during inference persist as unsupported); focused suites 89 passed, pending independent review; see the [B3 report](phase-b3-verification.md#stale-citation-correction--29-october-2026). The focused-frontend regression gap is since closed with a test-only per-assertion wait budget (focused 72/72 in three consecutive runs, full frontend 120/120, lint/typecheck clean, no application change); see the [closure section](phase-b3-verification.md#frontend-regression-closure--29-october-2026).

B4 adds owner-managed follow-up tasks (`followups.py`), 2 schemas, 4 owner API routes, a lead-detail task panel, and regression coverage across backend, frontend and browser. Tasks run a validated open → in_progress → in_review → done lifecycle with cancel/reopen, CHECK-bound outcomes on terminal done, read-time overdue flags, and per-task history; content edits invalidate pending review. Tasks are internal-only with no sent/delivered state, no scheduler and no model involvement; the agent-run draft/approval flow and seven-tool registry are unchanged. Final B4 executed evidence, including the browser run (7 passed / 1 skipped, 11.8m, exit 0), is in its scoped report; B1/B2/B3 results remain historical. Independently identified acceptance defects (review-invalidation bypass, thin history snapshots, unlocked creation reads) are since corrected and verified; see the [corrections section](phase-b4-verification.md#acceptance-defect-corrections--29-october-2026). All B4 work is verified but uncommitted on main/ea0ea18.
