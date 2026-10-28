# Phase 5 verification — 27 October 2026

Implemented all seven internal business tools with Pydantic input/output contracts and an explicit registry:

- get_leads
- get_property_matches
- get_available_slots
- create_followup_draft
- request_approval
- book_site_visit
- get_run_status

The executor uses the existing server-only PostgreSQL connection, parameterized values, fixed handlers/identifiers, and server-controlled run context. Drafts cannot send messages. Approvals are created pending and cannot be approved through any tool. Booking requires an approved request with an unchanged snapshot and a fresh locked availability check. Mutation idempotency, real persisted IDs/statuses, safe errors, and post-commit success reporting are implemented. No schema migration, new dependency, public write endpoint, or frontend change was required.

## Executed commands

From backend/:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.check_tools
.\.venv\Scripts\python.exe -m scripts.database verify
```

Final results:

- **94 passed, 5 skipped**, one upstream Starlette/AnyIO deprecation warning, in 130.57 seconds.
- The passed suite includes **17 real Supabase rollback-only tool cases**. The original five dedicated-empty-database schema cases remain skipped without TEST_DATABASE_URL.
- `pip check`: no broken requirements found.
- Read-only smoke check: exit 0, status ok, all seven registry names, 3 leads in the fixed demo week, 1 property match for the first seed lead, 6 available appointment slots.
- Database verify: exit 0, one applied migration, original 4 demo leads / 3 properties / 6 slots intact.
- Follow-up read-only SQL confirmed zero leftover fixture runs and zero leftover fixture leads.
- `git diff --check`: passed.

Unit tests verify allowlisted names and schemas, argument bounds/timezones, rejected approval targets and status overrides, protection of server run context, rejection before database access, safe database errors, and failure to commit without reporting success.

Real Supabase tests verify date filtering, matching/no matches, deterministic ordering and truncation, missing records, draft persistence and duplicate-key behavior, reviewable pending snapshots, non-approved booking refusal, wrong-kind approval refusal, approved booking/consumption/replay, unavailable/disabled/elapsed slots, changed approved details, cross-run approval refusal, persisted status/count reporting, and terminal-run write refusal.

All live test writes were inside forced-rollback outer transactions. Simulated owner decisions existed only in test fixtures; no production owner decision route was added. No customer message was sent, no test booking was retained, no model call was needed, and no permanent demo run was created by the read-only smoke script.

## Remaining scope

Phase 6 must connect real Meta planning/tool selection to these actual tool results and persist execution steps. Phase 7 must add authenticated owner decisions, immutable approval lifecycle guards, durable retry/recovery, terminal-run replay rules, administrative slot-edit protection, and concurrency tests. The Phase 8 dashboard is not implemented. Frontend checks were not repeated because its files and dependencies were unchanged.

`docs/tools.md` contains setup assumptions, commands, expected outputs, tool contracts, and what to share if a command fails. No additional environment variables are required for Phase 5 beyond the configured database/TLS settings.
