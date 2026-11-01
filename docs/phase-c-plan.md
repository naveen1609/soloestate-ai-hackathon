# Phase C: website chat — approved 30 October 2026, closed 1 November 2026

**Closure: PASS**, independently accepted by Codex after the final timeout fix; the owner approved the local checkpoint. [Evidence](phase-c-verification.md#independent-acceptance-and-closure--codex-1-november-2026) distinguishes independent checks from Muse's broader runs and preserves the earlier failures. This supersedes the historical implementation-only/no-commit authorization below for the closure checkpoint alone. No public deployment, customer contact, external delivery or Phase D implementation is authorized.

Status: **approved for bounded local implementation and fictional automated testing only, 30 October 2026.** This contract concretizes [Phase B milestone 7](phase-b-plan.md) (channel adapters, website chat first) against the implemented B1–B5 foundation. Refined 30 October 2026 to close identity, privacy, provenance, grounding and acceptance gaps; implementation clarifications below are part of the approved contract. This authorizes local development and fictional testing only — not public deployment, real-customer contact, WhatsApp, email delivery, autonomous booking, later phases, commits, pushes or production data changes.

Product intent: a website visitor can open a public chat page, be captured as an isolated lead, ask property questions answered through the existing grounded FAQ pipeline, see provisional availability, and record visit interest that the owner fulfills through the existing owner flows. All persistence reuses the B1–B5 tables and services; no new tables, tools, schedulers or delivery integrations belong to Phase C.

## Implementation clarifications — approved 30 October 2026

These five rulings are part of the contract and were recorded before any implementation. Nothing here widens scope.

### 1. Token validation

- Strict shape: `v1.<base64url(payload)>.<base64url(mac)>`, total length at most 512 characters, exactly three dot-separated segments, version exactly `v1`. The payload must JSON-decode to exactly `{cid, iat, exp}` — `cid` a canonical UUID string, `iat`/`exp` integers, no extra keys, `exp - iat` at most 86400 seconds.
- Verification order: shape/size → version → payload types → lifetime bounds → MAC over the full `v1.<payload>` segment with constant-time comparison → expiry (`exp` not passed) → conversation row lookup. Authentication completes before any record lookup; lookup failures after successful authentication answer 404 without distinguishing reasons.
- Secret: `VISITOR_TOKEN_SECRET` is environment-only with no repo default; the length check (minimum 32 characters) is a configuration sanity check, not entropy proof. The owner generates it locally with a cryptographically random generator (for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`), stores it in the ignored backend `.env`, and never prints, logs, reports or commits the value. Mint and verify both fail closed (503 for misconfiguration, never a degraded unsigned mode).

### 2. Retry identity

- A send takes ONE client key (the append key). The draft operation key is derived server-side as `chat:draft:{message_id}` after the append commits, with the internal draft request carrying the deterministic post-append version — retries reproduce the identical key and fingerprint without trusting the client.
- At most one assistant reply per persisted customer message on the send path: the derived key replays the stored reply (no new rows, no repeated inference) across retries, concurrent same-key sends and crash recovery. No drafted-state check exists in `draft()` and none is added (minimal-change rule); an owner-initiated draft on the same message follows unchanged B3 semantics and is outside the send invariant.
- Replay consumes no rate allowance: replayed appends insert no rows, so the trailing-hour customer-message count is unchanged, and replayed drafts return before any model call. Chat-start replay returns the identical isolated lead, conversation and originally minted token (the collision alias is generated once, never regenerated).
- New request keys on an already-answered message never create a second reply; a new append key always means a new customer message with its own reply.

### 3. Privacy wording

- The contract's "no association" rule is reconciled as follows: the ONLY permitted contact with pre-existing lead rows is the internal normalized-email existence check inside the creation transaction. It returns no record, history, archive status or collision detail to any caller; the code path branches only between identical-response outcomes (direct insert versus alias insert), and race losers fall into the alias path via the database UNIQUE backstop.
- Visitor responses use explicit public schemas only: conversation id, version, status, own messages (id, role, body, citations, versions, timestamps), own lead id plus visitor-supplied requirements, matches snapshots and slot windows. Internal notes, operation evidence, request keys, owner credentials and unrelated data are never serialized to visitors.

### 4. Shared B3 pipeline

- `draft()` remains the grounding choke point. The only compatible changes are truthful provenance threading: an `actor` parameter defaulting to `'owner'` on `crm.mutate`, `crm.save`, `conversation.log_activity`, `conversation.append` (plus an optional rate-limit parameter, default off), `conversation.draft`, `conversation.handoff` and `qualification.activity`, validated server-side against `('owner','visitor')` (anything else is a programmer error, never client-reachable). Owner routes pass nothing and behave byte-identically. `open_conversation` is deliberately NOT refactored: chat start needs lead+conversation creation in ONE transaction (separate `mutate` calls open separate connections), so it issues the 3-line conversation INSERT plus the shared `log_activity` call inline with a pointer comment instead of duplicating logic or touching B3 behavior.
- No change to gate order, citation validation, assembly, unsupported text, transitions, version checks, timeouts or model-call code. Clients cannot choose actor, message role (fixed `customer`), lead binding (token row) or model authority (existing client only).

### 5. Scope of abuse protection

- The per-conversation rate cap bounds one thread only. It does not limit chat-start volume, total conversations, or aggregate inference spend, and Phase C adds no CAPTCHA, IP reputation, global budgets or production monitoring. This increment is a local fictional-data slice and must never be described as ready for public deployment; limitations carry this explicitly.

## Reuse inventory (no parallel architecture)

- `backend/app/conversation.py`: conversation open/append/draft/handoff/review semantics, citation validation, server-assembled grounded answers, unsupported handling and gates — unchanged; the visitor send path calls the same `draft()` choke point.
- `backend/app/faq.py`: versioned FAQs, deterministic scope/keyword retrieval — unchanged, owner-managed only.
- `backend/app/qualification.py`: `property_matches` shared matcher and requirement validation — unchanged, called with the token-bound lead only.
- `backend/app/tools/service.py` `get_available_slots` logic: reused read-only for provisional slot windows; no new tool, no booking authority.
- `soloestate.conversations`, `conversation_messages`, `leads`, `crm_operations`, `lead_activities`: existing rows and triggers; visitor writes record truthful `actor='visitor'` evidence (see migration 0010).
- Owner lead-detail conversation panel: visitor conversations are visible to the owner with no UI change; all owner review/handoff/resolve actions stay owner-only.
- Existing patterns reused as-is: `crm.mutate` idempotency shapes plus an actor parameter, `sameOrigin` BFF checks, BFF body-size bounds, HttpOnly SameSite=strict cookies, `useMutation` unknown-outcome retry.

## C1. Visitor identity and token handling

- Chat authentication uses stateless HMAC-SHA256 visitor tokens minted by one backend endpoint: format `v1.<base64url(payload)>.<base64url(mac)>`, payload `{cid, iat, exp}`, where `cid` is exactly one conversation id. The MAC covers the full `v1.<payload>` string. No new authentication framework: verification mirrors the existing hash-compare discipline, with no token table and no migration.
- Secret configuration: new `VISITOR_TOKEN_SECRET` backend setting, environment-only, minimum 32 bytes of entropy, no repo default; when missing or short, every chat endpoint fails closed (503, no token minted, no chat state touched). The secret never reaches the browser, logs or errors.
- TTL is 24 hours from issue. A token authorizes exactly the conversation in its payload: the backend loads that conversation row and re-derives the lead binding from the row — the token carries no lead id and confers no lead choice.
- Verification order and failures: malformed shape or bad base64 → 401; MAC mismatch via constant-time compare (forged) → 401; `exp` passed → 401; conversation missing → 404 without distinguishing reasons; token valid but path conversation differs → 403. All rejections persist nothing.
- Visitor tokens are unrecognized by the unchanged owner dependency (no `sess_` prefix, no owner credential) so every owner endpoint answers 401; chat endpoints accept only visitor tokens and ignore the owner cookie entirely.
- BFF cookie `soloestate_visitor`: HttpOnly, SameSite=strict, Secure on HTTPS, Path `/api/chat`, Max-Age 86400. Chat mutations require same-origin POSTs through the new BFF route; oversized bodies are rejected by the existing size bound.
- Revocation before expiry is out of scope for stateless tokens; the owner freezes a chat instantly by resolving its conversation through the existing UI (resolved blocks messaging and drafting via existing gates). This trade-off is accepted for the bounded increment.

## C2. Lead capture and association (no email oracle)

Email is self-asserted and proves nothing. Phase C therefore performs NO automatic association with existing leads:

- Every chat start creates a fresh isolated lead plus a fresh conversation. Name and email are validated exactly like owner lead creation; the new lead carries status new, journey new and contact unknown, with no consent, opt-in or requirement claims.
- Email collision (matching an existing OR archived address — handled identically) must still satisfy the database UNIQUE email constraint without disclosing anything: the stored email becomes a transparent plus-suffixed alias (`local+chat-<8 hex>@domain`, local part truncated only to respect the 254-character bound, regenerated on the rare alias collision with at most 3 attempts before a safe 503), while the asserted address is preserved verbatim in server-written lead notes. The visitor-visible success response is identical whether the address was new, existing or archived; timing differences are not part of the acceptance bar.
- No reads of existing lead rows beyond the internal normalized-email existence check inside the creation transaction (which never surfaces), no writes to any pre-existing lead, and no access to any previous conversation: repeat submissions and shared emails each yield separate isolated leads and conversations. Owner reconciliation across duplicates uses existing flows only; any future merge is out of scope and must preserve the strictest contact state.
- Chat performs no delivery, booking or approval, so unknown-permission visitor leads create no contact surface and no opt-out bypass; leads are never auto-opted-in.
- No email verification, external messaging or new authentication integration is added; the residual email-sharing exposure is eliminated by construction (nothing existing is ever linked or shown).

## C3. Conversation and messaging flow

- Endpoints (all token-scoped, versioned, idempotent through client request keys): chat start (isolated lead creation + conversation open + token issue), resume (own conversation plus bounded messages), requirements update (own token-bound lead only: budget, localities, bedrooms, timeframe with owner-identical validation; no journey/contact/status/notes/fingerprint/review changes — qualification review stays owner-only), send (append one `customer` message under the client key, then draft exactly one assistant reply under the server-derived `chat:draft:{message_id}` key through the shared pipeline), read (own thread with citations and superseded flags).
- Requirements updates need no origin marker: token-bound leads are visitor-created by construction (tokens mint only at chat start) and cross-conversation tokens are rejected, so no other lead is reachable.
- Optimistic versions are enforced as in B3; a stale send returns the current state for resync instead of silently overwriting. Replaying an identical send returns the stored reply; changed-payload key reuse is rejected.
- Visitor writes persist `actor='visitor'` operation evidence; visitor conversation opens and unsupported-answer reviews log `actor='visitor'` activity through the existing shapes. Owner handoff/review actions on visitor conversations keep logging as owner.

## C4. Grounding, persistence and failure handling

The B3 grounding correction is authoritative and the visitor path cannot bypass it: sends call the same `draft()` function, in order — retrieval-membership check, pinned active-version check, exact-quote containment check, commit-time freshness revalidation under `FOR SHARE` row locks, server-side assembly within the storage bound, fixed unsupported text with reason codes otherwise. Citation validity alone is never sufficient: structural defects, inference failures, timeouts and malformed model output all persist the fixed unsupported text, model prose is never persisted as grounded, and the `pending_owner` transition fires on every unsupported outcome exactly as in B3.

- Unsupported questions → fixed text, reason code, `pending_owner`, visitor owner-review notice.
- Model timeouts/failures → identical unsupported path; no partial assistant message is ever persisted (the draft commit writes exactly one message row or nothing).
- Duplicate sends → idempotent replay of the stored reply with no new rows.
- Rate-limit rejections → 429 with retry guidance; booking, activity and operation records unchanged.
- Crash between append and draft leaves a resumable orphan customer message, never an auto-filled reply: resending the same two keys completes exactly one reply per customer message. The UI distinguishes unknown sends (key lookup before retry, mirroring `useMutation`) from confirmed states, and refetches explicitly after sends rather than inventing realtime streams.

## C5. Property questions, availability and visit intent

- Property answers reuse retrieval and the shared matcher against the token-bound lead's current requirements (supplied in-chat via C3); explanations carry the same requested/actual snapshots and version pins. Timeframe and readiness rules are unchanged. Because nothing existing is ever linked, no cross-visitor criteria exposure is possible.
- Availability is a read-only provisional slot listing (explicit 1–31 day window, bounded page) reusing the existing availability logic; it implies no reservation.
- Visit intent is captured as ordinary conversation messages with no keyword heuristics or special-cased replies: where FAQs ground the question the visitor gets a grounded answer, and otherwise the fixed unsupported text plus the owner-review notice constitutes the guidance reply and the owner follow-up trigger. Phase C creates NO appointment requests, approvals, drafts-as-consent or bookings from visitor input. Booking remains owner-executed through the existing consent flow. No automatic handoff or status change follows intent; the owner sees the conversation in the existing panel. A visitor may separately request human handoff exactly along the existing `none` → `requested` edge; resolving handoff stays owner-only.

## C6. Rate and abuse safeguards

- SQL-enforced per-conversation cap of 30 customer messages per trailing hour, counted inside the send transaction after the conversation `FOR UPDATE` lock so concurrent sends serialize; over-cap sends are rejected 429 with nothing persisted. Concurrent sends with distinct keys append in commit order and each drafts its own reply; no merging or cancellation.
- Existing 5000-character body CHECKs, explicit token TTL and scope checks, fixed BFF method/path/query allowlists, same-origin chat mutations, BFF body-size bounds, and no owner data beyond the token's own conversation. No IP-based rules (no proxy trust is invented).
- These endpoint safeguards are not the full owner policy engine (a separately authorized milestone): no per-agent capabilities, spend limits or emergency stop are added here.

## C7. Migration 0010 (justified, no new tables)

`0010_visitor_actor.sql` widens the `actor` CHECK on `crm_operations` and `lead_activities` from `('owner')` to `('owner','visitor')`. It is necessary, not optional: `conversation_messages.request_key` and `lead_activities.request_key` carry deferred foreign keys to `crm_operations(request_key)`, so every visitor send MUST persist operation rows, and recording them as `owner` would fabricate owner actions. Owner provenance is preserved because owner endpoints keep writing `actor='owner'` through unchanged defaults, visitor tokens cannot reach owner endpoints, and the actor value is server-assigned from the authenticated endpoint — never client-supplied.

- Backward compatibility: every existing row already satisfies the widened CHECK. The migration drops the two single-value CHECKs and adds the widened ones; it adds no columns, tables, seeds or triggers, and rewrites no data.
- Transaction integrity and RLS: visitor writes use the same mutate shapes plus the actor parameter with identical atomicity (failure rolls back message, activity and operation together); RLS and PUBLIC revocation are untouched and the backend remains the trusted boundary.
- Required migration tests: fresh empty-database apply (ledger reaches 10 with 0010), rejection of any third actor value, legacy owner rows passing the new CHECKs, end-to-end visitor operation/activity FK chains, and rollback behavior on activity failure.

## C8. UI

- Public `/chat` page requiring no owner session: lead-capture form, chat thread reusing message/citation rendering, in-chat requirements capture, and explicit states for grounded, unsupported, owner review, handoff requested, resolved and failures. The UI renders confirmed backend records only and never invents outcomes, availability or delivery.
- New BFF route `/api/chat/[...path]` with a strict allowlist mirroring the chat endpoints; owner BFF paths and cookies are untouched.

## Exclusions

Live WhatsApp or any external customer messaging; email verification; lead merge/dedupe tooling; autonomous follow-ups, schedulers, retries-as-side-effects or notifications; new model tools, broader model mutation permissions or autonomous booking authority; a general automation engine; rewrites of B1–B5 or a parallel conversation store; visitor access to anything outside C1–C6 (including lead search, approvals, bookings, drafts, decisions, journey, qualification review, tasks, FAQ management); realtime sockets or polling streams; production identity, deployment or data-retention claims.

## Acceptance criteria

- Chat start with valid name/email opens exactly one isolated lead and conversation and returns a working token; invalid input is rejected; existing, archived and repeat emails produce responses indistinguishable from new ones, with only the internal existence check touching pre-existing rows and no writes to, history leakage from, or disclosure about any pre-existing lead. Visitor responses match the public schemas exactly.
- Forged, expired, malformed and cross-conversation tokens are rejected (401/401/401/403) with nothing persisted; visitor tokens on owner endpoints get 401 and owner sessions gain nothing on chat endpoints.
- Repeat token resumes the same thread with its history; tokenless restarts create isolated conversations; versioned sends enforce stale-resync; identical replay returns stored replies; changed-payload reuse is rejected.
- The 31st hourly customer message is rejected 429 with all records unchanged; concurrent distinct-key sends serialize into ordered appends with one reply each; crash-then-resend completes exactly one reply per customer message.
- Grounded replies pass every B3 gate with pinned citations and exact quotes; unsupported, timeout and malformed-model cases persist fixed text with reason codes and move to `pending_owner`; pending/resolved/handoff states render their exact safe UI.
- Matches and availability reflect only the token-bound lead and explicit windows; visit intent and handoff requests create no request/approval/booking rows; owner review/resolve flows on visitor conversations work unchanged with `actor='owner'` evidence.
- Visitor operation/activity rows carry `actor='visitor'`; all pre-existing owner evidence keeps `actor='owner'`; migration 0010 applies cleanly with legacy rows intact.
- Fictional browser journey (chat entry → capture → grounded Q&A → requirements → matches → availability → visit intent → owner visibility of the same conversation in the lead panel) passes with exact cleanup of every visitor row by fixture prefix.
- No B1–B5 regression: the B3 conversation/FAQ suites, CRM suites covering the shared mutate path, full frontend, lint/typecheck/build and the existing browser specs stay green (full backend regression runs once because `mutate` gains the actor parameter).

## Acceptance-review corrections — 30 October 2026

The independent review returned five findings. The rulings below extend the contract; implementation follows them. Nothing here widens scope beyond the accepted property-question feature.

### A. Visitor-write freeze policy

- A resolved conversation rejects every new visitor write (send, requirements, property context, handoff request) with 409 and no persistence.
- `pending_owner` status or a requested handoff rejects visitor sends before any persistence, with no inference. Requirements and property-context updates stay allowed in those states: they are lead/conversation data rather than messaging or drafting, requirement changes reset the qualification fingerprint so the owner must re-review, and the visitor handoff request is the path to the owner. Handoff request on an already-requested handoff keeps the existing invalid-transition rejection.
- Every freeze check runs under the conversation row lock inside the same transaction as the write, so a concurrent owner resolve, acknowledge, reopen, handoff or review serializes instead of interleaving. Owner paths are unchanged: the guards are visitor-only flags or compatible defaulted parameters.
- Safe replay is preserved: guards live inside the mutate work phase after the prior-operation check, so replaying an already-committed operation returns the stored response without new writes and without re-gating.

### B. Send gating and blocked-after-append disclosure

- Visitor send gates (stale version, freeze states, rate cap) are enforced inside the append transaction before any message or operation row is written. Already-blocked sends persist nothing and perform no inference.
- Genuine crash recovery is preserved: an append that was authorized and committed before an interruption stays resumable, and resending the same keys completes exactly one reply.
- If conversation state changes between an authorized append and the draft commit, the send fails with an explicit 409 stating the customer message was saved and the chat must be reloaded; the UI refetches the thread on every send failure so retry, unknown and confirmed states always reflect backend truth.

### C. Token-scoped property context

- Visitor chats start with `property_id=NULL`, which limits retrieval to business FAQs. A new token-scoped `POST /chat/conversations/{id}/context` operation binds one property as the conversation's question context: request `{property_id}` only, versioned and idempotent, freeze-governed exactly like requirements (rejected only when resolved).
- The server validates that the property exists, is active, and appears in the token-bound lead's current matches recomputed server-side (limit 20); anything else is rejected 422. No FAQ ids, lead ids, owner actions or model authority are accepted from the visitor.
- The binding writes `conversations.property_id` with visitor activity evidence; the version trigger bumps the conversation, so a context change during inference rejects the in-flight draft at commit. When the commit-time `property_id` differs from the retrieval-time value, an explicit property-changed 409 takes precedence over the generic version error. Retrieval, citation validation, assembly and the unsupported path are unchanged; historical citations and message bodies stay pinned and immutable.
- The BFF allowlist gains the single context path and the UI gains a per-match selector plus a focus indicator; nothing else in the visitor surface changes.

### D. Draft claim serialization

- Each draft holds a session-level advisory claim on its request key across pre-check, inference and commit. A concurrent same-key retry waits on the claim (bounded 120s poll), then re-checks the committed operation row and replays without inference. Inference never begins unless the caller owns the claim: if the bound lapses, the caller re-checks once more, replays a completed operation after fingerprint validation, and otherwise fails retryably (503) without inference, without new rows and without touching the claim.
- Consequence assessment: one extra PostgreSQL session per in-flight draft holding no row locks and no open write transaction during inference (pre-check reads run on a separate connection that closes before the model call; the claim session holds only an idle transaction containing the lock statement itself). The claim releases explicitly in a finally block and automatically on disconnect, so claimant failure or crash always permits recovery. Lock ordering is claim → mutate advisory → row locks in every draft call, and no other path takes claim locks, so no deadlock cycle exists.
- Guaranteed: one persisted reply per persisted customer message; completed replay performs no new inference; no caller runs inference without owning the claim; recovery after claimant failure completes exactly one reply. Provider-level unknown outcomes (a response lost after the provider executed) are distinguished from these application guarantees; exactly-once external inference is not claimed.

### E. Migration-0010 test split

- CHECK behavior (owner/visitor accepted, third value rejected), legacy-row validity, visitor FK chains, activity-failure rollback and RLS/PUBLIC restrictions are executed where they can run read-only or under rollback fixtures. Fresh empty-database application stays in the dedicated-empty-database pattern and is reported unexecuted when `TEST_DATABASE_URL` is absent, with the exact configuration stated.

## Implementation record — 30 October 2026

Implemented on main / 0a94469 under the approved contract and clarifications above: `visitor_tokens.py`, `chat.py`, `chat_schemas.py`, `chat_api.py`, migration 0010, provenance threading on the shared paths, the `/api/chat/[...path]` BFF route, the public `/chat` page, and the token/database/BFF/component/browser regression suites. Two contract-compatible corrections landed during verification: the chat BFF path mirrors the full backend path after `/api/chat` (same convention as `/api/owner`), and the visitor token travels cookie-only (the BFF strips it from the start body into the HttpOnly cookie, so the client schema carries no token field). Visitor drafts use the production Meta integration; tests inject mocked model responses with no public mock-mode switch. Exact evidence is in [phase-c-verification.md](phase-c-verification.md). No scope, exclusion or acceptance change was needed.

## Test and demo plan

New backend `test_chat.py` (token mint/verify/forge/expiry/scope matrix including version/size/lifetime/type cases, fail-closed misconfiguration, validation, shape allowlists) and `test_chat_database.py` (isolation matrix, email no-disclosure and repeat/archived behavior, concurrency/idempotency/rate atomicity, duplicate-reply attempts with new keys, replay at the rate limit, crash-recovery resend, unsupported and model-failure flows, handoff-request edge, no-booking proof, actor attribution, rollback, owner-message compatibility, public-schema exclusion of notes/evidence/credentials); migration-0010 fresh-apply and CHECK-behavior cases in the empty-database pattern; frontend `chat.test.tsx` for capture/thread/states/unknown-send handling; BFF chat-route allowlist pins plus owner/chat cross-rejection cases; new `chat.spec.ts` browser journey with fixture-prefix cleanup across conversations, messages, operations, activities and the visitor lead. Because the shared `mutate` write path changes, run the full backend suite once after the final edit; run the full frontend suite and the full browser command once (existing specs unchanged, additive chat spec). Demo setup reuses the existing fictional seed plus owner-created FAQs; model-usage documentation gains the visitor-message data note at implementation time.
