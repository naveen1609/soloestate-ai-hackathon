# PostgreSQL and Supabase setup

The application uses six immutable migrations and deterministic fictional data. Live Supabase connectivity, checksummed setup, typed business tools, owner consent, booking integrity and session persistence have been verified. Five schema tests still require a separate empty disposable test database; see testing.md.

## Supabase project and connection

1. Create a dedicated Supabase hackathon project with an empty `soloestate` schema namespace. Save the database password privately. No real customer data is needed.
2. In **Connect**, copy the PostgreSQL **direct connection** URI. If the computer cannot reach the direct endpoint over IPv6, use the **session pooler** connection on port 5432, which supports IPv4. Copy the exact host and username from your project; do not guess either. Avoid transaction pooling (port 6543) for these migration scripts.
3. Replace the password placeholder locally. Percent-encode reserved characters in a URI password (for example `@` becomes `%40`). The PostgreSQL URI is different from the Supabase project HTTPS URL, anon key, and service-role key. None of those API keys is needed by Psycopg.
4. Download the server root CA certificate from your project's Database settings. Save it outside Git, and set its absolute path in `DATABASE_SSLROOTCERT`.
5. Append the settings below to **backend/.env** without replacing your existing Meta settings. Do not put them in frontend files or root environment examples.

```dotenv
DATABASE_URL="postgresql://YOUR_DB_USER:YOUR_ENCODED_PASSWORD@YOUR_DB_HOST:5432/postgres"
MIGRATION_DATABASE_URL=
DATABASE_SSLMODE=verify-full
DATABASE_SSLROOTCERT="C:/Users/Barat/Downloads/your-supabase-ca.crt"
```

`DATABASE_URL` is the server-side application connection. An optional `MIGRATION_DATABASE_URL` can hold a separate admin connection; migrate, seed, and verify use it when configured. Otherwise they use `DATABASE_URL`. The `check` command checks the application connection. The migration role needs schema/table creation privileges. Initially a dedicated hackathon project may use the project database owner through the backend; this broad database role must never be exposed to browsers. Before public deployment, provision a restricted runtime role and authenticated owner actions.

`verify-full` encrypts traffic and verifies the certificate and hostname. `require` is an explicit weaker option that encrypts but does not provide the same full verification guarantee; prefer `verify-full`. The code overrides URI SSL mode with `DATABASE_SSLMODE`, so a pasted `sslmode=disable` cannot silently weaken the configured setting. `disable` is accepted only for loopback hosts in local tests. Connection establishment is bounded to 10 seconds, statements to 15 seconds, and lock waits to 5 seconds. Automatic prepared statements are disabled for pooling compatibility.

Official references: [Supabase PostgreSQL connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [SSL enforcement](https://supabase.com/docs/guides/platform/ssl-enforcement).

## Apply and verify

Run these commands in PowerShell:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m scripts.database check
Write-Output "Check exit code: $LASTEXITCODE"
.\.venv\Scripts\python.exe -m scripts.database migrate
Write-Output "Migrate exit code: $LASTEXITCODE"
.\.venv\Scripts\python.exe -m scripts.database seed
Write-Output "Seed exit code: $LASTEXITCODE"
.\.venv\Scripts\python.exe -m scripts.database verify
Write-Output "Verify exit code: $LASTEXITCODE"
```

Stop at a nonzero exit code and resolve it before running the next command. Every successful command exits 0; errors exit 1. Expected first-run JSON:

```json
{"status":"ok","connected":true}
{"status":"ok","applied":["0001_initial.sql","0002_execution_integrity.sql","0003_dashboard_sessions.sql","0004_core_crm.sql","0005_customer_journey.sql","0006_lead_qualification.sql","0007_conversation_foundation.sql"]}
{"status":"ok","seeded":true}
{"status":"ok","migrations":7,"seed_records":{"leads":4,"properties":3,"visit_slots":6},"demo_week":"2026-11-02/2026-11-08","currency":"INR"}
```

Repeat `migrate`, `seed`, and `verify` to check repeatability. A repeated migration returns `applied: []`; seeds do not duplicate rows or overwrite existing edits. Missing seed IDs are restored when seeding again. The verification counts exact fictional IDs; it is not a full checksum of current business records.

Scripts load only SQL files shipped in this repository. They expose no option for arbitrary SQL or model-generated statements. A transaction-scoped advisory lock serializes setup commands, and the migration ledger records normalized SHA-256 checksums. Applied migrations must remain immutable; add a new numbered migration for changes. Edited seed files are rejected after initial application. SQL and ledger updates commit together; success is printed only after commit. There is deliberately no destructive reset command.

Use the repository runner as the migration source of truth. This is a `supabase/migrations` SQL layout, not a configured Supabase CLI project; applying these files separately through another migration manager will not populate the runner's checksum ledger.

## Tables and invariants

| Table | Purpose and constraints |
| --- | --- |
| `leads` | Fictional contacts, INR budget, bedrooms, legacy status; B1 separate journey, contact preference/permission and handoff state |
| `lead_activities` | B1 owner-only before/after state, reason, timestamp and deferred CRM operation reference; RLS, no browser policy |
| `faqs` | B3 business/property scope, status, current version; reuses the version trigger |
| `faq_versions` | B3 immutable question/answer rows keyed by FAQ and version; deferred CRM operation reference |
| `conversations` | B3 lead link, optional property context, independent status/handoff state; reuses the version trigger |
| `conversation_messages` | B3 append-only role/body rows with grounding state, pinned citations and deferred operation reference |
| `followup_tasks` | B4 owner-managed title/notes, due timestamp, assignee label, status lifecycle and outcome; reuses the version trigger |
| `properties` | Fictional inventory with positive INR prices and bedrooms; active flag |
| `visit_slots` | Owner's exact appointment windows in UTC; valid time range and no overlaps |
| `runs` | Goal, request idempotency key, state, structured plan and outcome JSON objects |
| `run_steps` | Registered tool name, ordered step, typed-argument/result JSON objects, terminal evidence, per-run idempotency key |
| `followup_drafts` | Draft-first email content, version, status, per-run idempotency key |
| `approvals` | Follow-up or booking target, payload snapshot, decision state/time; follow-up targets must match draft's run and lead |
| `bookings` | Run, lead, property, slot and matching booking approval; one confirmed booking per slot and one booking per approval |
| `approval_decisions` | Authenticated owner decision evidence, review hash and idempotency key |
| `dashboard_sessions` | Hashed single-use login tickets and expiring, revocable owner sessions |
| `crm_operations` | Committed owner operations, request fingerprints, retry results and audit provenance |
| `schema_migrations` / `seed_history` | Committed setup history and checksums |

UUIDs are assigned by application code; fixed UUIDs identify seed records. All timestamps use `timestamptz`. Money uses `numeric(14,2)` and is INR throughout this demo. Tool matching is computed from lead preferences and inventory in Phase 5; there is no synthetic matches table.

The one-person owner can attend one appointment at a time. Slots are property-independent, nonoverlapping exact windows, and bookings choose a property. Half-open `[start,end)` ranges allow adjacent slots. A partial unique index on confirmed booking slot IDs prevents duplicate active bookings, including concurrent insert attempts; cancellation releases a slot. PostgreSQL provides these [range exclusion and uniqueness constraints](https://www.postgresql.org/docs/18/rangetypes.html). No extension is required for the single-range exclusion constraint.

Owner authentication, immutable snapshots, version/hash binding, approval transitions and fresh availability checks are enforced by backend transactions and database triggers. Confirmed slot edits are guarded. Composite foreign keys bind bookings to their approved targets; additional triggers validate recorded consent. The agent can reach guarded booking only through authenticated owner execution. No message sender exists.

## Privacy and access

- `soloestate` is a private schema. Leave it out of Supabase's exposed Data API schemas.
- Schema/table access from `PUBLIC` is revoked. RLS is enabled on all eighteen tables with no browser access policies. Newly created schema defaults must remain private.
- No anon/authenticated role grants, browser Supabase client, public database endpoint, or service-role API key is added.
- Database owners bypass RLS. RLS here is an additional browser-access barrier, not owner authorization or multi-tenancy. The backend is the trusted boundary.
- Restrict project access, database network access, and deployment secrets appropriately. Never log DSNs or share certificate private keys (the downloaded server CA certificate is public).

## Fictional demo snapshot

The seed has four fictional contacts using `example.invalid`, three fictional Bengaluru properties, and six half-hour appointments. Three leads fall in **2–8 November 2026**; one is from the preceding week. One lead intentionally has no exact budget match. Seed data contains no completed runs, approvals, sent messages, or bookings.

The demo snapshot is fixed, not relative to today's date. Later orchestration/demo steps must supply the explicit demo week rather than pretending these are current leads or shifting timestamps silently. Display times in Asia/Kolkata; SQL stores UTC. The seeded appointments are 5 and 6 November at 10:00, 10:30, and 14:00 IST.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

The Phase 4 baseline was **58 passed, 5 skipped** (one upstream warning). Current test commands and coverage are in `testing.md`. Unit tests cover TLS configuration, secret redaction, missing/invalid credentials, migration drift and newline normalization, and truthful failure when commit fails. PostgreSQL grammar parsing validates migration and seed SQL syntax. Unit/parser tests do not execute PostgreSQL DDL or prove database integrity.

Five real database tests cover schema/seed repeatability, budgets and foreign keys, overlapping slots, booking uniqueness and approval binding, and RLS/schema privacy. They require a **dedicated empty test database**, with no `soloestate` schema. Tests refuse an existing application schema and roll back their schema and records. Never point them at production or the seeded app project. Test connection settings come from the terminal, not backend/.env:

```powershell
$env:TEST_DATABASE_URL = 'YOUR_DEDICATED_EMPTY_TEST_DATABASE_URI'
$env:TEST_DATABASE_SSLMODE = 'verify-full'
$env:TEST_DATABASE_SSLROOTCERT = 'C:/path/to/test-project-ca.crt'
.\.venv\Scripts\python.exe -m pytest -q -m database
```

Expected when configured: **5 passed**, no skips. For local loopback PostgreSQL, use `TEST_DATABASE_SSLMODE=disable`. These tests do not establish full runtime approval enforcement or orchestration, which are later phases.

## If a command fails

Share the exact command, safe JSON error output, and exit code. Do not share `.env`, database URI, password, API keys, or raw driver exceptions. A missing `DATABASE_URL` gives a `configuration` error. Other connection/SQL failures report `database_error` and SQLSTATE where available; they never report success.

Check URI format and password encoding, copied host/user, direct IPv6 versus session-pooler IPv4 access, project availability, CA certificate path, TLS mode, migration privileges, and migration order. A checksum mismatch requires restoring an applied migration and adding a new migration; do not edit the checksum ledger to conceal drift.

## Resolved configuration errors

A certificate path must have one matching pair of quotes, for example `DATABASE_SSLROOTCERT='C:/path/to/certificate.crt'`. Copying a quoted Windows path inside another pair of quotes produces invalid dotenv syntax. Settings now fail immediately with a safe line-number error rather than silently skipping that line.

Percent-encode reserved characters in the URI password before inserting it in the connection string. An unencoded `@` can be misinterpreted as part of the hostname, causing a misleading DNS failure. The connection helper now detects that malformed host and gives a safe configuration error. Actual credentials remain only in the ignored backend environment file.

Historical Phase 7 baseline: the second immutable migration added worker state/leases/resume limits and owner decision evidence. Approval/draft/booking/slot triggers enforce the invariants described in owner-recovery.md. That baseline had two applied migrations and unchanged fictional seed counts; it is not the current migration total.

Historical Phase 8 baseline: migration 0003_dashboard_sessions.sql added hashed, expiring one-time tickets and revocable owner browser sessions. At that stage there were twelve private RLS-enabled tables and three applied migrations, with unchanged fictional seed counts. The current Phase A totals follow below.

Phase A update: migration 0004_core_crm.sql adds archive/version metadata, owner-operation provenance, replacement-booking linkage, cancellation reason and the private crm_operations audit/idempotency table. Phase A verification expected four migrations and thirteen RLS-enabled tables; B1 now adds a fifth migration and fourteenth table. Previously applied migrations and seed checksums remain unchanged. See phase-a.md.

## B1 additive schema

Migration `0005_customer_journey.sql` adds separate journey/contact/handoff columns to leads and private RLS `lead_activities` (fourteen tables including ledgers). See [B1 contract](phase-b-plan.md#b1-contract). Activity and CRM operation evidence commit together via a deferred foreign key. Applied migrations 0001–0004 are unchanged. Existing rows receive new/unspecified/unknown/none defaults without consent claims or backfilled history. Run the existing migrate command once before starting updated code; never rerun seeds to obtain B1 defaults.

## B2 qualification schema

Migration 0006_lead_qualification.sql expands nullability on the original lead budget/locality/bedroom columns, adds nullable purchase_timeframe and qualification_fingerprint, and enforces a nonnegative ceiling independently when the floor is unknown. Existing nonnegative/order/nonempty/positive-bedroom checks still govern supplied values. No new table, duplicate profile, record rewrite or seed change; there remain fourteen private RLS tables. Existing timeframe/review values are unknown, not backfilled claims.

The server validates finite two-decimal INR amounts, bounded nonempty localities and strict 1–30 bedrooms. NULL represents an unanswered question; explicit no_fixed_deadline is a known timeframe answer. A positive ceiling is needed for readiness. Backend-owned fingerprints reference canonical requirement facts; requirement edits clear current review and record history atomically. No API accepts a client fingerprint. Migrations 0001–0005 remain immutable. [Contract](phase-b-plan.md#b2-contract--approved-29-october-2026), [evidence](phase-b2-verification.md).

## B3 conversation schema

Migration `0007_conversation_foundation.sql` adds private RLS `faqs`, `faq_versions`, `conversations` and `conversation_messages` (eighteen tables including ledgers) and attaches the existing version trigger to the two mutable tables. FAQ versions are append-only with a current pointer; archiving only removes a FAQ from retrieval. Messages store role, grounding state, pinned citations and model provenance with no update path. Conversation lifecycle entries reuse `lead_activities` snapshots without touching lead handoff state. Migrations 0001–0006 remain immutable. [Contract](phase-b-plan.md#b3-contract--approved-29-october-2026), [evidence](phase-b3-verification.md).

## B4 task schema

Migration `0008_followup_tasks.sql` adds private RLS `followup_tasks` (nineteen tables including ledgers) and attaches the existing version trigger. Tasks carry a lead link with optional property context, title/notes, an optional due timestamp, a free-text assignee label, a five-state lifecycle and an outcome that a CHECK binds to done status only. Overdue derives at read from due date versus current time. Task lifecycle entries reuse `lead_activities` snapshots without touching lead handoff state. Migrations 0001–0007 remain immutable. [Contract](phase-b-plan.md#b4-contract--approved-29-october-2026), [evidence](phase-b4-verification.md).
