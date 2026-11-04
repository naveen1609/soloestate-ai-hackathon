# SoloEstate AI Growth Engine-lite

Modular, ground-truth-verified property marketing intelligence and content generation workspace for SoloEstate AI.

---

## 🚀 Key Capabilities & Workflow

1. **Owner Marketing Brief & Voice Input**
   - Spoken audio capture and structured brief parsing powered by **Meta Muse Voice Transcribe** (`muse-voice-transcribe-1.0`).
   - Converts natural voice inputs into structured campaign parameters (`target_audience`, `key_selling_points`, `tone`, `campaign_objective`, `special_offers`).
   - Built-in demo presets for festive campaigns and family lifestyle marketing.

2. **Property Visual Intelligence**
   - Accesses authentic property photography through read-only interfaces.
   - Segments architectural features (balconies, modular kitchens, windows, hardwood flooring) using **Meta SAM 3.1** (`sam-3.1`).
   - Tags detected elements with confidence scores and normalized bounding boxes to feed visual creative prompts without misrepresenting the property.

3. **Creative Concept Studio**
   - Generates marketing cards and social media visual concepts using **Meta Muse Image** (`muse-image-1.0`).
   - Preserves authentic photography untouched.
   - Automatically stamps all generated assets with: `AI Creative Concept - Not Actual Property Photography`.

4. **Marketing Intelligence & Multi-Platform Copywriting**
   - Muse Spark (`muse-spark-1.3`) can select a copy template per channel. The server renders the copy from pinned CRM facts.
   - The owner can save and preview another template before approving it. Current CRM references are checked again at approval and export.
   - Campaign Planner: Formulates audience targeting specs, placement suggestions, and daily budget recommendations within owner-configured caps.

5. **Local Pipeline Orchestration**
   - End-to-end multi-model workflow orchestration managed cleanly by **LocalPipelineOrchestrator** (`source="local_pipeline"`).
   - Coordinates the 7-step pipeline in a single click with real-time execution duration tracing and authentic attribution.

6. **Owner Approval & Asset Export Workspace**
   - Generated items are saved as `draft`.
   - Owners can preview a server-rendered copy template and approve or reject individual assets.
   - Strict separation of concerns: Content approval NEVER authorizes live advertising spend or external publication (`launch_authorized = false`).
   - Clean export package generation (formatted JSON / clipboard copy) for marketing teams, strictly excluding draft, rejected, or superseded items.

---

## 🛡️ Architectural Isolation & Boundaries

- **Zero Core File Mutation**: No changes were made to D1, D2, or D3 codebase, CRM business logic, shared conversation services, or authentication.
- **Separate Migrations**: All Growth Engine database changes reside in `backend/app/growth_engine/migrations/` (0001 through 0005) and use an independent tracker table (`soloestate.growth_migrations`). Core `supabase/migrations/` remains untouched.
- **BFF Isolation**: Growth Engine has its own Next.js BFF proxy at `frontend/src/app/api/growth-engine/[...path]/route.ts`.

---

## 🧪 Testing & Verification

Provider inference in the verification record uses fixtures. For the database
setup and committed HTTP restart test, follow the [disposable PostgreSQL runbook](docs/growth-engine-architecture.md).

### Run Backend Unit & API Tests:
```powershell
# From backend directory
$env:PYTHONPATH="."
.\.venv\Scripts\pytest.exe tests/growth_engine/test_growth_api.py tests/growth_engine/test_growth_approvals.py tests/growth_engine/test_growth_grounding.py tests/growth_engine/test_growth_marketing_and_orchestrator.py tests/growth_engine/test_growth_visual_and_creative.py tests/growth_engine/test_growth_voice.py -v
```
**Result**: 49 passed across all service layers (grounding, voice, SAM 3.1, Muse Image, Muse Spark, approvals, API router).

### Run Real Database Integrity Tests (Optional):
```powershell
.\.venv\Scripts\pytest.exe tests/growth_engine/test_growth_database.py -v --run-integrity-db-tests
```
**Result**: 5 passed (or 5 skipped safely in local mode without flag).

### Run Frontend Vitest Component & Route Tests:
```powershell
# From frontend directory
npm test -- --run
npm run typecheck
```
**Result**: All 250 frontend tests across 18 test files pass cleanly; 0 TypeScript errors.

---

## 📋 File Deliverables Summary

### Backend:
- `backend/app/growth_engine/__init__.py`
- `backend/app/growth_engine/config.py` — Settings, model IDs, budget caps.
- `backend/app/growth_engine/schemas.py` — Typed Pydantic models for briefs, grounding, SAM 3.1, creatives, copy, plans, and exports.
- `backend/app/growth_engine/migrations/0001_growth_engine_initial.sql` — Isolated database schema and RLS policies.
- `backend/app/growth_engine/migrations/0002_growth_engine_durable_kit.sql` — Durable kit and foreign key updates.
- `backend/app/growth_engine/migrations/0003_growth_engine_provenance_and_concurrency.sql` — Provenance constraint alignment and concurrency fields.
- `backend/app/growth_engine/migrations/0004_growth_engine_concurrency_and_photo_deletion.sql` — Single active campaign plan unique partial index & compound photo FK deletion trigger.
- `backend/app/growth_engine/migrator.py` — Isolated migration runner.
- `backend/app/growth_engine/fixtures.py` — Demo property photos and audio brief presets.
- `backend/app/growth_engine/store.py` — Targeted single-entity persistence store for kits, briefs, and assets.
- `backend/app/growth_engine/router.py` — FastAPI endpoints under `/owner/growth-engine/*`.
- `backend/app/growth_engine/app.py` — Standalone FastAPI app mounting growth engine router.
- `backend/app/growth_engine/services/grounding_service.py` — Fact freezing and claim verification.
- `backend/app/growth_engine/services/voice_service.py` — Meta Muse Voice Transcribe service.
- `backend/app/growth_engine/services/visual_intelligence_service.py` — Meta SAM 3.1 segmentation service.
- `backend/app/growth_engine/services/creative_service.py` — Meta Muse Image generation service.
- `backend/app/growth_engine/services/marketing_intelligence_service.py` — Meta Muse Spark copy & campaign planner.
- `backend/app/growth_engine/services/orchestrator_service.py` — LocalPipelineOrchestrator pipeline orchestrator.
- `backend/app/growth_engine/services/approval_service.py` — Review, edit, approval, and export service.
- `backend/tests/growth_engine/` — 6 test suites covering all services, grounding, database, and endpoints.

### Frontend:
- `frontend/src/app/growth-engine/page.tsx` — Owner Growth Engine Workspace page.
- `frontend/src/app/growth-engine/growth.css` — Dedicated styles matching SoloEstate UI.
- `frontend/src/app/api/growth-engine/[...path]/route.ts` — Isolated BFF route handler.
- `frontend/src/app/api/growth-engine/[...path]/route.test.ts` — Vitest permission gate test.
- `frontend/src/lib/growth-engine/types.ts` — TypeScript definitions.
- `frontend/src/lib/growth-engine/api.ts` — Client API helpers.
- `frontend/src/components/growth-engine/growth-workspace.tsx` — Main interactive workspace.
- `frontend/src/components/growth-engine/voice-brief-panel.tsx` — Voice brief recorder & structured parser.
- `frontend/src/components/growth-engine/visual-intelligence-panel.tsx` — Authentic photo viewer & SAM 3.1 visualizer.
- `frontend/src/components/growth-engine/creative-studio-panel.tsx` — Muse Image concept cards & watermark badges.
- `frontend/src/components/growth-engine/content-studio-panel.tsx` — Platform copy switcher & grounding badge.
- `frontend/src/components/growth-engine/campaign-planner-panel.tsx` — Strategy & budget guidance with spend guardrails.
- `frontend/src/components/growth-engine/approval-export-panel.tsx` — Approval workspace & JSON exporter.
- `frontend/src/components/growth-engine/__tests__/growth-workspace.test.tsx` — Vitest component tests.

### Documentation:
- `docs/growth-engine-architecture.md` — Detailed technical architecture and integration spec.
- `README-GROWTH-ENGINE.md` — Complete module guide.
