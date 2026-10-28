# Five-minute demo runbook

This runbook presents real model use, persisted business evidence and owner control. Start both services and sign in using the [README](../README.md). Keep keys, sign-in fragments, cookies, developer tools and the backend .env out of recordings. All displayed leads and properties are fictional.

## Before presenting

From backend:

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_preflight --services
```

Expected: status ok, three migrations, seed counts 4/3/6, real lead/match/slot read counts and both health checks. This check does not call Meta. Zero slots is a valid current result; skip visit proposals if none remain. If it fails, share the failing check name and sanitized JSON; fix that check before presenting.

Generate a private sign-in link:

```powershell
.\.venv\Scripts\python.exe -m scripts.dashboard_login
```

Open it locally within two minutes. A consumed/expired link needs replacement. Do not paste it into a presentation or issue. The browser session lasts up to eight hours; sign-out revokes it. Use the 127.0.0.1 host consistently.

## 0:00–0:45 — Problem and owner goal

Explain: one owner needs to prioritize leads, prepare follow-ups and coordinate visits while retaining control of customer-facing actions. Show the goal input and its read-only/draft-first permissions. The demo uses the explicit fictional week, not this week's real customers.

For the live demonstration choose **Read only** and enter:

> Review the fictional leads created during 2–8 November 2026 in Asia/Kolkata. Read the property matches for Aarav Demo and the available visit slots during that fixed week. Finish with the actual succeeded step sequences. Do not draft, request approval, send messages or book visits.

Click Run goal once. This consumes real Meta credits. Allow for model/network latency; the server can return a safe failure instead of a result. Do not click a new goal to resolve an uncertain response: use Check saved run or retry the saved key.

## 0:45–2:15 — Plan, execution and evidence

Show the structured proposed plan and backend-confirmed timeline. Explain that Meta selects tools and observes their results, while fixed Python handlers validate arguments and execute SQL safely. Expand one actual tool result.

Expected fictional data, if unchanged: three leads in the date window, one Cedar Apartment match for Aarav, and six initially seeded future visit options. These are evidence expectations, not hardcoded success results. Slots may already be disabled/booked/elapsed, and empty availability is truthful behavior.

Show the lead/property cards and outcome counts. Read-only work creates no drafts, approvals, messages or bookings. Completion means validated plan execution; the report does not independently certify every interpretation of the owner's goal.

## 2:15–3:30 — Draft-first approval stop

On the configured demonstration database, select the retained original draft-first run in Recent work, or add this run ID as a query parameter before the sign-in URL's fragment:

`608afe1f-33e9-4ee7-b1c0-4fa9cb225252`

This is clearly labeled **persisted historical execution**, not a fresh model request. It shows three reviewed leads, two matches, actual invalid approval attempts followed by model correction, one saved draft and an owner approval stop. Open Review to display recipient, full subject/body, version and snapshot. Close it without deciding unless the human owner explicitly chooses to approve or reject.

A fresh repository/database has no retained execution run. Generate a fresh approval demo from backend instead:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --request-key soloestate-draft-demo-v1
```

The CLI's built-in demo goal compares leads/matches/slots, prepares ONE follow-up draft, requests approval and stops. Expected status: awaiting_approval, one draft, one pending approval, zero bookings and zero messages. This is a real billable model run; inspect the returned status rather than assuming it reached the intended stop. Repeating the identical command replays a stopped report; do not change the goal or permission mode with the same key. There is no destructive reset.

## 3:30–4:15 — Owner choice and booking integrity

Explain the actual controls:

- Approve draft stores approved consent/content; it does not send a message. Reject stops continuation. The backend binds decisions to the exact reviewed payload and records audit evidence.
- Continue run is explicit after a decision. A booking needs its own booking approval snapshot; a follow-up approval cannot authorize a booking.
- At booking time the backend locks and rechecks the slot and details. A stale slot or changed property prevents the booking. A UI availability card is not a reservation.

Do not imply the original pending approval has been decided or a booking exists. For a deterministic demonstration of rejection and competing bookings, show the [automated integrity evidence](phase-9-verification.md); those tests use isolated fictional records and cleanup rather than altering the retained owner decision.

## 4:15–5:00 — Outcome and limitations

Show persisted status after refreshing and the verified counts. Explain the real Meta response IDs/model metadata, idempotent request keys and bounded recovery. Summarize the limits: no message delivery or external calendar integration, one owner, fixed fictional dates, no background worker, and five schema checks still require a separate empty database. See [limitations](limitations.md).

## Repeatable CLI alternative

From backend, for a new live read-only run:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --read-only --request-key phase10-readonly-demo-20261028-v1 --goal 'Review the fictional leads created during 2–8 November 2026 in Asia/Kolkata. Read the property matches for Aarav Demo and the available visit slots during that fixed week. Finish with the actual succeeded step sequences. Do not draft, request approval, send messages or book visits.'
```

The exact same goal/mode/key replays its stored completed/paused/failed report and does not start a new model activation. For a deliberate fresh demo change only the key to a new value. For an uncertain response, inspect first:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --lookup-key phase10-readonly-demo-20261028-v1
```

The lookup is read-only and does not call Meta. Read reason/status, verified_steps and real counts. Do not convert a failure or absent result into a success claim. Recovery uses explicit resume/recover controls and existing lease/retry bounds; see [owner-recovery.md](owner-recovery.md).

## If connectivity fails during the presentation

Keep the error visible, explain what is unconfirmed, and show the retained historical run with its date and real evidence. Do not substitute a fake model response. Share only the command, exit code and sanitized error/check name. Meta errors may reflect credentials, credits, access or timeouts; database errors may reflect connectivity/TLS/privileges. Regenerate expired sign-in links privately. Stop servers with Ctrl+C when finished.
