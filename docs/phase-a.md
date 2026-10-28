# Phase A: Core CRM and appointment management

## Live workspaces

- `/`: current business totals, recent leads, upcoming confirmed visits and pending owner appointment requests.
- `/leads`, `/leads/new`, `/leads/{id}`: paginated search, status/archive filters, details, create/edit, archive/restore and related appointment/draft evidence.
- `/properties`, `/properties/new`, `/properties/{id}`: paginated inventory search, active/locality/bedroom/price filters, details and create/edit/deactivate.
- `/appointments`, `/appointments/new`, `/appointments/{id}`: search, status/date filters, owner review, booking, rescheduling and cancellation.
- `/agent`: the existing Muse Spark goal, plan, timeline, run history and agent approval dashboard. It shares the same live leads, properties and slots.

## Start/update

Existing configuration is sufficient. Stop your normal backend/frontend with Ctrl+C before updating their running code; an old process can continue serving old routes. From backend:

```powershell
.\.venv\Scripts\python.exe -m scripts.database migrate
.\.venv\Scripts\python.exe -m scripts.database verify
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: four immutable migration entries, seed IDs still present (4 leads, 3 properties, 6 slots), API startup complete. Migration 0004 is additive; it neither resets records nor changes old migrations. In another terminal, from frontend:

```powershell
npm.cmd run build
npm.cmd run start
```

Expected: production build and Next.js ready. From another backend terminal:

```powershell
.\.venv\Scripts\python.exe -m scripts.dashboard_login
```

Open the private single-use URL within two minutes. Existing `?run=UUID` links still lead to the execution workspace after sign-in. Browser authentication remains HttpOnly; permanent owner, database and model credentials stay server-side.

## Owner walkthrough

1. Create a fictional lead with email, preferred localities, budgets and bedroom count. Edit notes/status. Search and filter the lead list. Archive hides a lead from ordinary lists and new agent matching/drafting/booking; restore preserves its identity and history.
2. Create an active fictional property. Edit its price/details, search by locality and filter inventory. Deactivation preserves referenced appointments and approvals. Changes to reviewed business data require fresh booking consent.
3. From a lead choose Schedule visit. Select a lead/property, load future available slots for a window of up to 31 days, or create a nonoverlapping future slot (positive duration, at most eight hours). Times entered in datetime controls use the browser's local timezone; displayed evidence uses Asia/Kolkata.
4. Prepare owner review. This saves a pending request, not a reservation. Check recipient, property/price and exact visit interval. Approve and book explicitly records owner consent and commits the booking after another availability/snapshot check. Reject leaves no booking.
5. On a confirmed appointment choose Reschedule visit. Select a new slot and review the replacement. Only a successful transaction cancels the original and creates an approved linked replacement. A stale/conflicting replacement leaves the original confirmed. Its protected slot is never edited in place.
6. Cancel from the appointment detail with an explicit reason. The operation is versioned/idempotent, recorded in the owner operation log and releases the slot. No customer message or external calendar event is sent.

Active leads means nonarchived leads; closed status remains distinct from archival. Past visits are not automatically marked attended/completed. Appointment statuses remain confirmed/cancelled in this phase.

## API and persistence

All CRM endpoints require the existing owner authorization. The Next.js proxy admits only explicit method/path/query combinations; it does not expose arbitrary backend paths or SQL.

| Method | Backend endpoint | Purpose |
| --- | --- | --- |
| GET | `/owner/crm/summary` | Current business metrics and operational records |
| GET, POST | `/owner/crm/leads` | Search/list or create |
| GET, PATCH | `/owner/crm/leads/{id}` | Details or versioned full-field edit |
| POST | `/owner/crm/leads/{id}/archive` | Archive/restore |
| GET, POST | `/owner/crm/properties` | Search/list or create |
| GET, PATCH | `/owner/crm/properties/{id}` | Details or versioned full-field edit/deactivate |
| GET | `/owner/crm/appointments`, `/owner/crm/appointments/{id}` | Current bookings with joined customer/property/time details |
| POST | `/owner/crm/appointments/{id}/cancel` | Versioned explicit cancellation |
| GET, POST | `/owner/crm/visit-slots` | Provisional availability or controlled future slot creation |
| POST | `/owner/crm/appointment-requests` | Prepare booking/replacement review |
| GET | `/owner/crm/appointment-requests/{id}` | Restore a persisted owner review |
| POST | `/owner/crm/appointment-requests/{id}/commit` | Explicit consent and atomic commit |
| POST | `/owner/crm/appointment-requests/{id}/reject` | Explicit rejection |
| GET | `/owner/crm/operations/{request_key}` | Confirm a previously committed operation after response loss |

Lists accept bounded page/page_size, allowlisted sort and relevant filters. Sorts are repository constants, values are parameterized, and missing records return 404. Validation errors return 422; stale/conflicting mutations return 409. Unknown database/network outcomes remain unconfirmed. Lock contention can return 503 and must be retried using the saved request.

Writes require a stable request_key; edits/cancellation also require expected_version. Reusing a key with different arguments is rejected. Forms save the exact pending request in this tab's sessionStorage and check the operation log after ambiguous responses. Do not start a new duplicate action to resolve a timeout.

Existing bookings/visit_slots remain the appointment model. Migration 0004 adds lead archive metadata, update versions/timestamps, replacement linkage, cancellation reason, owner/agent provenance and `crm_operations` (RLS-protected owner audit/idempotency results). There are thirteen private tables. No duplicate appointments table exists.

Owner appointment workflows use existing runs only as provenance/consent containers with origin=owner, no model trace and no invented AI steps. They are omitted from the AI run/approval lists and cannot be resumed by the model runner. Owner requests are restored from the CRM overview. Existing agent runs, evidence, identifiers and immutable snapshots remain intact. Booking confirmation reuses the original approval service and registered booking tool. No new model tool or model consent authority is introduced.

## Verification and failures

From backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --run-tool-db-tests --run-agent-db-tests --run-integrity-db-tests
.\.venv\Scripts\python.exe -m pip check
```

From frontend:

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
npm.cmd run test:e2e
```

Browser CRM checks use real HTTP/database writes on uniquely named fictional records, then delete their exact records. No existing demo database is reset. The live Meta test is still opt-in. See phase-a-verification.md for actual results rather than assuming an expected count passed.

If a command fails, share the exact command, failing test/route, exit code and sanitized error. Keep credentials, sign-in links, cookies and .env private. After a lost response use the saved request key and operation lookup. Reload on a stale version before making a new edit.

## Scope boundaries

No WhatsApp/email delivery, customer website conversations, background scheduling, autonomous agent network, task queues or FAQ module was added. Future customer-journey architecture is in phase-b-plan.md. The local single-owner deployment limitations still apply.
