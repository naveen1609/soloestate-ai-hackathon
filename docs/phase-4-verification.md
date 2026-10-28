# Phase 4 verification — 27 October 2026

Implemented:

- `supabase/migrations/0001_initial.sql`: private single-owner schema, eight business tables, checks, foreign keys, idempotency constraints, slot range exclusion, confirmed-booking uniqueness, RLS and PUBLIC privilege revocation.
- `supabase/seed.sql`: four fictional leads, three fictional properties, six visit slots; fixed IDs/timestamps and no real contact details.
- Server-only PostgreSQL settings and Psycopg 3.3.6 driver.
- Transactional, checksummed migration/seed runner with setup history and locking.
- Safe `check`, `migrate`, `seed`, and `verify` CLI commands; no raw SQL CLI input.
- Supabase/TLS setup, schema documentation, and five opt-in PostgreSQL integration tests.

Executed from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.database check
.\.venv\Scripts\python.exe -m scripts.database migrate
```

Results:

- Psycopg and development-only pglast parser installation succeeded.
- **54 tests passed, 5 skipped**. One upstream Starlette/AnyIO warning remains.
- Both migration and seed SQL passed PostgreSQL grammar parsing through pglast 8.7.
- Unit tests cover connection/TLS options, safe errors, configuration, immutable migration drift, CRLF normalization, and failure to commit without false success output.
- Dependency check: no broken requirements found.
- `check` and `migrate`: safe configuration errors and exit code 1 because `DATABASE_URL` is absent. No database operation succeeded or was claimed to succeed.
- No frontend source/dependency changes; frontend checks were not repeated.

The five real PostgreSQL tests are skipped without `TEST_DATABASE_URL`. They cover repeatable migrations/seed, budget and foreign-key constraints, nonoverlapping slots, booking uniqueness and approval binding, and RLS/schema privacy. Parser and unit tests do not establish successful DDL execution or real transaction/concurrency behavior.

Environment limitations: no psql or Supabase CLI found; Docker daemon unavailable; no database URI configured. Supabase itself was not contacted, migrations were not applied remotely, and fictional records have not been inserted into a live database.

Next required verification: follow `database.md`, set backend-only database/TLS settings, then run check → migrate → seed → verify. Expected counts are 4 leads, 3 properties and 6 slots. Run the five integration tests separately against a dedicated empty test database. Full approval workflows, owner authentication, and business tools remain later phases.

## Subsequent live Supabase verification

After the owner configured a project, diagnostics identified two local environment issues: doubled surrounding quotes on the certificate path, and an unencoded `@` in the URI password that caused a malformed hostname. The ignored backend/.env was corrected without displaying credentials. Certificate-path existence was confirmed; TLS verification remained enabled.

Executed successfully against the configured Supabase project:

- `python -m scripts.database check`: `status: ok`, `connected: true`, exit code 0.
- `python -m scripts.database migrate`: applied `0001_initial.sql`, exit code 0.
- `python -m scripts.database seed`: committed fictional records, exit code 0.
- `python -m scripts.database verify`: one migration, four demo leads, three demo properties, six demo slots, exit code 0.
- Repeated migrate/seed/verify: `applied: []`, successful seed, same counts; no duplicate demo rows.

Added fail-fast dotenv syntax validation with safe line numbers and rejection of URI hosts containing an unescaped password `@`. Four regression tests cover these errors and correct Windows certificate-path quoting. Final result: **58 passed, 5 skipped**, no broken dependencies. The five dedicated-empty-database integrity tests are still skipped; live setup/repeatability is confirmed, but those separate integrity scenarios are not claimed as executed.

The initial missing-connection limitation above is historical and is now resolved. Full runtime approval enforcement and business tools remain later phases.
