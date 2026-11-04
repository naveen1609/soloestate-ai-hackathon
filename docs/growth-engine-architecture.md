# SoloEstate AI Growth Engine-lite — Technical Architecture & Integration Specification

## 1. Overview & Objective
Growth Engine-lite is an independent, modular property marketing intelligence and content generation workspace developed in parallel with Phase D3. It provides an owner-directed pipeline for transforming verified real estate CRM listings and owner marketing briefs into multi-platform social media kits, visual concepts, and campaign strategies.

### Core Tenet: Strict Grounding & Anti-Hallucination
The engine operates strictly on frozen property facts extracted from the CRM (`soloestate.properties`). Copy uses pinned CRM bedroom count, locality and listed price. The server renders the same structured revision at approval and export. Visual observations can guide concept illustrations and remain separate from CRM facts.

---

## 2. Multi-Model Meta Integration Matrix

| Role | Meta Model Identifier | Functionality & API Protocol | Integration Status |
| :--- | :--- | :--- | :--- |
| **Owner Voice Brief** | `muse-voice-transcribe-1.0` | Spoken audio transcription & structured extraction. Protocol: Meta Voice `/v1/asr/transcribe` multipart transport (`request` JSON + `audio` WAV PCM). Enforces WAV container validation and 10MB upload limits. | **Contract fixture tested.** Live inference unverified. |
| **Visual Feature Intelligence** | `sam-3.1` | Identifies architectural & layout elements from authentic photography. Protocol: Meta SAM 3.1 `POST /v1/responses`. Validates coordinates `[ymin, xmin, ymax, xmax]`, bounds, dimensions, and ordering strictly without fabrication. | **Contract fixture tested.** Live inference unverified. |
| **Creative Concept Generation** | `muse-image-1.0` | Generates marketing cards and social graphics grounded in verified features. Stamped with `AI Creative Concept` watermark. | **Provider fixture tested.** Live inference unverified. |
| **Content Intelligence & Planning** | `muse-spark-1.3` | Selects an allowlisted template per channel. Copy and campaign fields are rendered server-side. Campaign planning runs locally. | **Provider fixture tested.** Live inference unverified. |
| **Workflow Orchestration** | `local_pipeline` (`LocalPipelineOrchestrator`) | Coordinates the multi-stage pipeline, providing execution traces and duration metrics. | **Implemented & Verified** local orchestration pipeline. |

---

## 3. Strict Architectural Isolation & Boundaries

```
[Core SoloEstate CRM / D1 / D2 / D3]
  │
  │ Read-only documented API: CRM().listing('properties') / CRM().detail('properties', id)
  ▼
[Growth Engine-lite Module: backend/app/growth_engine/]
  ├── Grounding Service (Freezes snapshot & verifies claims)
  ├── Voice Service (Meta Muse Voice Transcribe)
  ├── Visual Intelligence Service (Meta SAM 3.1)
  ├── Creative Service (Meta Muse Image)
  ├── Marketing Intelligence Service (Meta Muse Spark)
  ├── Orchestrator Service (LocalPipelineOrchestrator)
  ├── Approval & Export Service
  └── Isolated Database Tables: soloestate.growth_*
```

### Architectural Boundaries Enforced:
1. **Zero Core File Mutation**: No modifications made to `0001` through `0019` migrations, D1/D2/D3 policies, core booking logic, or shared conversation services.
2. **Separated Migrations**: Growth Engine maintains its own isolated migration tracker (`soloestate.growth_migrations`) and migration files under `backend/app/growth_engine/migrations/` (migrations `0001` through `0005`).
3. **No External Publishing or Ad Spend**: Content approval is strictly separated from publishing. Staged ad launch payloads are explicitly tagged as `launch_authorized = false`.
4. **Single Active Campaign Plan & Safe Photo Deletion**: Migration `0004` enforces a single active plan per kit via partial index `growth_campaign_plans_single_active_idx` and manages photo deletions cleanly via `trg_growth_property_image_delete` setting `source_image_id = NULL` while preserving non-null property IDs and creative assets.
5. **Channel Concurrency & Active Content Serialization**: Migration `0005` enforces a single active content item per kit channel via partial index `growth_content_items_single_active_idx` on `(kit_id, channel) WHERE status != 'superseded'`, coupled with transaction-scoped PostgreSQL advisory locks (`growth:content:{kit_id}:{channel}`) during channel content replacement.

---

## 4. Database Schema Design

The module adds 6 isolated tables in the `soloestate` schema, protected by Row Level Security:

1. `soloestate.growth_migrations`: Tracks applied Growth Engine migrations independently.
2. `soloestate.growth_property_images`: Links authentic property photography and architectural tags to CRM properties.
3. `soloestate.growth_briefs`: Stores owner voice or text briefs with structured marketing goals.
4. `soloestate.growth_kits`: Holds the marketing kit container bound to an immutable `grounding_snapshot` and SHA-256 hash.
5. `soloestate.growth_creative_assets`: Stores SAM 3.1 segmentation data and Muse Image concepts (`is_authentic = false` with concept watermark).
6. `soloestate.growth_content_items`: Holds platform-specific drafts (IG, FB, Festive) with `grounding_verified` status and approval timestamps.
7. `soloestate.growth_campaign_plans`: Stores audience specifications, recommended placements, and daily budget recommendations.

---

## 5. API Contract & Integration Points

All endpoints are mounted under `/owner/growth-engine/` and protected by `Depends(require_owner)`:

- `GET /owner/growth-engine/properties`: List properties with authentic image counts and grounding status.
- `GET /owner/growth-engine/properties/{property_id}/images`: Fetch authentic property photos.
- `POST /owner/growth-engine/transcribe-voice`: Transcribe audio via Meta Muse Voice.
- `POST /owner/growth-engine/visual-segment`: Segment architectural features via Meta SAM 3.1.
- `POST /owner/growth-engine/generate-creative`: Generate concepts via Meta Muse Image.
- `POST /owner/growth-engine/generate-content`: Generate copy drafts via Meta Muse Spark.
- `POST /owner/growth-engine/generate-plan`: Generate campaign strategies via Meta Muse Spark.
- `POST /owner/growth-engine/orchestrate-kit`: Full pipeline orchestration via LocalPipelineOrchestrator.
- `GET /owner/growth-engine/kits/{kit_id}`: Retrieve kit details.
- `PATCH /owner/growth-engine/kits/{kit_id}/content-items/{item_id}/decision`: Review, edit in-place, and approve/reject copy.
- `PATCH /owner/growth-engine/kits/{kit_id}/creative-assets/{asset_id}/decision`: Review and approve/reject creative concepts.
- `PATCH /owner/growth-engine/kits/{kit_id}/campaign-plan/decision`: Review and approve/reject strategy.
- `GET /owner/growth-engine/kits/{kit_id}/export`: Export approved kit bundle.

Frontend BFF proxy: `frontend/src/app/api/growth-engine/[...path]/route.ts`.
Frontend UI workspace: `frontend/src/app/growth-engine/page.tsx`.

---

## 6. Migration CLI Runner & Disposable PostgreSQL Setup

Growth Engine provides a dedicated, production-safe database CLI runner: `backend/scripts/growth_database.py`.

### Migration Runner Capabilities:
- `python -m scripts.growth_database check`: Checks the database connection.
- `python -m scripts.growth_database migrate`: Applies pending migrations `0001` through `0005` in order.
- `python -m scripts.growth_database verify`: Verifies schema objects, indices, and triggers.

Safety Guard: The CLI blocks execution if targeted against the production/shared Supabase URL (`db.vxdkchvabptqjtsqasdu.supabase.co`) unless explicitly permitted.

### Disposable PostgreSQL Setup (Docker / Local):
1. **Start disposable container**:
   ```bash
   docker run --name growth-engine-test-postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres -p 5433:5432 -d postgres:16-alpine
   ```
2. **Apply migrations**:
   ```powershell
   cd backend
   $env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/postgres"
   $env:DATABASE_URL = $env:TEST_DATABASE_URL
   $env:MIGRATION_DATABASE_URL = $env:TEST_DATABASE_URL
   $env:DATABASE_SSLMODE = "disable"
   .venv\Scripts\python.exe -m scripts.database migrate
   .venv\Scripts\python.exe -m scripts.growth_database migrate
   .venv\Scripts\python.exe -m scripts.growth_database verify
   ```
3. **Run concurrency, migration upgrade, and authenticated process-restart tests**:
   ```powershell
   $env:TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5433/postgres"
   .venv\Scripts\python.exe -m pytest tests/growth_engine/test_growth_database.py --run-integrity-db-tests -v
   ```
The Growth runner recognizes the two audited contents of migration 0005. The original
version could only apply when no duplicate current revisions existed. A database
that already recorded that version retains its original ledger entry. A database
still at 0004 reconciles duplicates as part of applying the current 0005.

4. **Tear down disposable container**:
   ```bash
   docker stop growth-engine-test-postgres && docker rm growth-engine-test-postgres
   ```
