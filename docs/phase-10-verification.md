# Phase 10 verification — 28 October 2026

Completed setup, architecture, model-usage, limitations, test-reference and repeatable demonstration documentation. The README now describes the implemented application rather than old phase scaffolding. Current operational database/tool/agent documentation reflects three migrations and the present tests. The root environment example now directs users to application-specific examples instead of suggesting unused Supabase API keys.

## Deliverables

- [README](../README.md): exact Windows setup, configuration, startup, sign-in, checks and failure reporting.
- [Architecture](architecture.md): request/agent/approval sequence, trust boundaries, persistence, booking integrity, recovery and limits.
- [Model usage](model-usage.md): real Meta integration, native tools, model responsibilities, validation, data and provider metadata.
- [Demo runbook](demo.md): five-minute presentation, fresh/read-only and draft-first paths, stable-key replay, historical-evidence fallback and safe failure behavior.
- [Limitations](limitations.md): honest local-only scope, fixed data, absent external delivery/calendar, deployment work and unexecuted schema checks.
- `backend/scripts/demo_preflight.py`: credential presence, read-only migration/seed verification and fixed read-only tools; optional local service health. It never invokes Meta, issues tickets or writes business records. PostgreSQL connections explicitly use READ ONLY transactions.
- [demo-evidence.json](demo-evidence.json): sanitized real run IDs, provider metadata, succeeded step identities, zero-mutation counts and replay confirmation. It contains no secrets or session links.

## Executed verification

From backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.demo_preflight
.\.venv\Scripts\python.exe -m scripts.demo_preflight --services
.\.venv\Scripts\python.exe -m pytest -q tests/test_demo_preflight.py
```

Default backend suite: **128 passed, 55 skipped**, one upstream Starlette/AnyIO warning; no broken requirements. Five new preflight cases passed, including redaction, failure gating, absent settings, optional unhealthy service checks and truthful model-connectivity reporting. The final READ ONLY transaction version passed its five tests and the live services preflight.

Preflight reported **status ok**, three migration checksums, seed counts **4 leads / 3 properties / 6 slots**, three leads in the demo window, one match for the seed lead, six currently available provisional slots and successful backend/frontend health checks. It explicitly reported model_connectivity=not_tested and business_writes=false.

The business database suites were not redundantly rerun for documentation/preflight changes. Phase 9 executed **173 distinct backend cases** in groups, including every configured business tool, agent, integrity and session database case. The five added preflight cases bring current unique executed coverage across those increments to **178**. A single full-suite invocation of 178 passes is an expected reproducible result, not claimed as executed in Phase 10. Five separate empty-database schema cases remain unexecuted.

From frontend:

```powershell
npm test
npm run lint
npm run typecheck
```

**38 passed**; lint and typecheck exited zero. The existing Phase 9 production build was started successfully for this rehearsal; frontend runtime code did not change in Phase 10. Phase 9's production build and six normal browser cases remain documented in its evidence.

## Live end-to-end rehearsal

The exact read-only CLI goal/key in [demo.md](demo.md) was executed against real Meta and PostgreSQL:

- Run `c256c25f-3e92-4252-b77f-657d3c8a23c9`, request key `phase10-readonly-demo-20261028-v1`.
- Status **completed**, reason validated_plan_finished.
- **6 real muse-spark-1.3 responses**, four succeeded steps: get_leads, get_property_matches, get_available_slots, get_run_status.
- Three fictional leads, one Aarav/Cedar match, six provisional slots; zero drafts, approvals, bookings and messages.
- Repeating the identical CLI goal/mode/key returned JSON equal to the original persisted outcome. Step IDs and model trace stayed unchanged. The stored outcome remained equal after replay.

The explicit live browser case also ran:

```powershell
$env:SOLOESTATE_LIVE_UI='1'
npm run test:e2e -- --grep 'runs a real read-only goal'
Remove-Item Env:SOLOESTATE_LIVE_UI
```

**1 passed in 54.3 seconds**. Run `c92b6805-a4eb-476a-8a10-3ab92b89b45c` completed after three real muse-spark-1.3 responses and one succeeded get_leads step. It had zero drafts, approvals, bookings and messages. This exercised local sign-in, Next.js BFF, FastAPI, real Meta inference, tool execution, PostgreSQL persistence and dashboard outcome rendering. The browser signed out afterward.

Original run `608afe1f-33e9-4ee7-b1c0-4fa9cb225252` retained **one pending approval and zero owner decisions**. No booking or customer message was committed during these rehearsals. Live read-only runs are intentionally retained for demonstration. Local validation services were stopped afterward.

## Remaining external work

No new configuration is required from the current owner. This phase does not deploy the app, create a Git commit/push, publish a video or submit to the hackathon. Official event rules and submission requirements must be verified before an actual submission. The complete local demo is ready; production deployment/identity and the separate empty-database tests remain explicitly documented limits.
