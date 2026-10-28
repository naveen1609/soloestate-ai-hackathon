# Phase 8 dashboard

The Next.js dashboard displays persisted plans, live tool steps, lead/property results, provisional visit slots, owner reviews and verified outcome counts. Goal submissions and continuation call the real FastAPI agent and Meta model. Polling refreshes evidence every eight seconds. Completion describes validated plan execution; it does not independently certify every interpretation of the business goal.

## Start locally

Use separate PowerShell terminals. From the backend directory:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\backend'
.\.venv\Scripts\python.exe -m scripts.database migrate
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: three checksummed migrations are applied or already present; Uvicorn reports startup complete. Existing seed data is preserved. Migration 0003 adds private dashboard session records with RLS.

In the frontend terminal:

```powershell
Set-Location 'D:\programing_tools\Meta Global AI Developer Hackathon\soloestate-ai-hackathon\frontend'
npm ci
npm run build
npm run start
```

Expected: a successful production build and Next.js ready at http://127.0.0.1:3000. For development use `npm run dev` instead of build/start. Server-only BACKEND_URL defaults to http://127.0.0.1:8000.

In a third terminal, from backend:

```powershell
.\.venv\Scripts\python.exe -m scripts.dashboard_login
```

Open the returned URL locally within two minutes. It is single-use: keep it private. An eight-hour owner session is stored in an opaque HttpOnly, SameSite Strict cookie. A new link is required after expiration or sign-out. The permanent owner credential, Meta key and database URL remain on the backend. Tickets and sessions are stored as hashes; the URL fragment is removed before exchange and is not sent in the page HTTP request. Use HTTPS when deploying so the cookie also has its Secure flag.

## Repeatable walkthrough

1. Sign in. Select the retained draft-first run in Recent work to inspect its real plan, failed calls followed by recovery, matching properties, provisional slots and pending review. Existing demo run ID: `608afe1f-33e9-4ee7-b1c0-4fa9cb225252`. A sign-in URL can include `?run=UUID` before its fragment.
2. For a harmless model demonstration choose Read only. Ask to review fictional leads created during 2–8 November 2026 in Asia/Kolkata and report verified results. Click Run goal. Meta usage consumes configured model credits.
3. Watch the backend-confirmed timeline and report. Read-only permissions prevent drafts, approvals and bookings. The demo uses a fixed fictional week; it does not pretend these are current-week records.
4. For draft-first work use the demo goal. Review opens the full immutable recipient/content or booking snapshot. Approve or Reject records only the explicit choice bound to its payload hash. Approving a draft does not send it: there is no message delivery integration. Booking approvals need Continue run and the backend's fresh slot check before a confirmed booking exists.
5. Continue run resumes approved work. Recover is offered for eligible interrupted work; active worker leases and retry limits are enforced by the backend. Signing out does not cancel a goal the backend already accepted.

Unknown network outcomes retain a stable request key. Use Check saved run or retry the saved request rather than starting an unrelated duplicate. Review retries retain their decision key; an uncertain decision blocks flipping to the opposite choice. Counts are shown only from backend evidence. Slots remain provisional, including when the dashboard previously displayed them as available.

## Checks and failures

From frontend:

```powershell
npm test
npm run lint
npm run typecheck
npm run test:e2e
```

The browser suite requires a running backend, configured owner/database credentials and installed Microsoft Edge. Stateful checks create isolated fictional runs, leads, properties, real tool evidence and pending drafts, then remove their exact fixture records even on assertion failure. Existing demo decisions can change freely; tests never decide those approvals. The real-model browser test is opt-in:

```powershell
$env:SOLOESTATE_LIVE_UI='1'
npm run test:e2e -- --grep 'runs a real read-only goal'
Remove-Item Env:SOLOESTATE_LIVE_UI
```

If a command fails, share its command, sanitized error, failed test name, browser error and corresponding backend/frontend logs. For sign-in failures issue a fresh link and check database migration/configuration. For a timeout retain the displayed request key and check the saved run first. Never share sign-in URLs, cookies, .env contents, API keys or database passwords. This is a single-owner local hackathon implementation; multi-owner accounts and production identity management are outside Phase 8.

Phase A: the existing execution dashboard is now at /agent. The sign-in link opens the live CRM overview at /. All original goal/run/approval behavior is retained. See phase-a.md for lead/property/appointment screens and operations.
