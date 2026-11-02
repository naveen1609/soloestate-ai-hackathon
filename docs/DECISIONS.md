# Architecture decision index

Created on 28 October 2026 from inspected source and existing documentation, with owner authorization. No earlier DECISIONS.md was present. Existing decisions below describe the implementation; they are not invented historical approval records.

## Existing architecture

- One shared PostgreSQL CRM: leads, properties, visit_slots and bookings. No parallel appointments table. Fixed server-side SQL, Pydantic validation and a seven-tool registry.
- Muse Spark proposes plans/tools; backend execution, persisted evidence and owner-only decisions determine outcomes. Model output cannot approve itself.
- Owner credentials and database credentials stay server-side. The Next.js BFF forwards only explicit authenticated method/path pairs.
- Booking snapshots and identity are protected. Replacement bookings cancel originals atomically; confirmed slots are not edited in place. Migrations are checksum-validated and additive.
- Source detail: [architecture](architecture.md), [database](database.md), [tools](tools.md), [Phase A](phase-a.md).

## B1 decisions (this increment)

- Reuse leads and their version trigger; add separate journey_stage, contact_preference, contact_permission and handoff_state. Do not reinterpret leads.status or bookings.status.
- New/legacy contact permission defaults to unknown, never inferred opt-in. Unknown preserves internal draft/booking behavior; no external delivery exists or is authorized.
- Opted-out or preference none blocks new drafts, follow-up/booking review and booking execution. Active handoff also pauses these actions for both owner and agent until explicit owner resolution. Existing bookings survive; rejection and cancellation remain available.
- The owner records permission evidence/reason. An opted-out lead cannot be reset to unknown; lifting it requires explicit opted_in evidence. This is an owner-recorded assertion, not independent verification of customer consent or a channel-specific legal permission system.
- Journey and handoff transitions are backend validated, owner-only, versioned and idempotent. No new model mutation tool. Later journey stages wait for supporting workflows; no attendance or contact is inferred.
- lead_activities records before/after B1 state, actor, reason, time and operation key in the same transaction. No API edits/deletes or backfilled events. Database administrators remain privileged; this is not a tamper-proof audit product.
- Contact actions share-lock the lead; state updates exclusively lock it. Approval snapshots remain immutable and unchanged. No consent snapshot is rewritten.
- Migration 0005 only; no applied migration edited, no seed changes, no new dependencies.

## B2 decisions — owner approved 29 October 2026

- Expand existing lead requirement columns to nullable through forward migration 0006; do not store placeholder customer preferences or create a parallel profile. Existing rows remain unchanged; new metadata defaults NULL.
- Matching needs complete core constraints; owner-confirmed qualification also needs timeframe. A known no-fixed-deadline answer is distinct from unknown. Qualification is not contact consent or appointment approval.
- Use a server-owned hash of canonical requirements as the current review marker. Semantic edits clear it, preserving old hash/facts in activity evidence; editing back does not silently requalify. Other B1 state changes do not invalidate the same requirements.
- Reuse one matcher across owner API and existing typed tool. Return deterministic explanations/version/time and distinguish incomplete input from no inventory. Preserve score meaning as a rule-based rank.
- Preserve omitted PATCH values; explicit NULL clears an answer. Bind provided-field identity for retries and retain the legacy digest fallback for pre-B2 lead-edit responses. Reuse transaction/version/audit machinery.
- No new model mutation tool, autonomous qualification, delivery, background worker or B3 feature.

## B3 decisions — owner approved 29 October 2026

- Version FAQs as current-pointer plus immutable append-only versions; archiving hides from retrieval without deleting history. No update/delete API paths exist for versions or messages; allowlist tests pin their absence (DB-level forbid triggers would break exact fixture cleanup).
- Pin exact cited versions on every grounded answer; derive superseded-citation flags at read time instead of rewriting history. A valid citation alone never proves semantic support: structural failures persist as unsupported with a reason code and fixed server text.
- Keep conversation lifecycle (open/pending_owner/resolved) and handoff (none/requested/resolved) as independent axes mirroring B1 vocabulary. Drafting requires open status with no requested handoff; unsupported answers move to pending owner; owner review actions are explicit and reasoned.
- Authorize retrieval to business FAQs plus the conversation's property context only, ranked deterministically by keyword overlap. Inference runs outside the database transaction; a replay pre-check and a re-validating commit phase prevent duplicate drafts and stale commits.
- Accept reason fields only where they are persisted (handoff/review activity); FAQ content versions are their own audit trail. Simulation never alters contact permission, journey stage or lead handoff, and works regardless of B1 opt-out because nothing is contacted or delivered.
- No new registry tool, channel, delivery, scheduler, vector search or autonomous behavior. [Contract](phase-b-plan.md#b3-contract--approved-29-october-2026), [verification](phase-b3-verification.md).
- Grounding correction: assemble grounded bodies server-side from validated quotes; never persist model prose as grounded. Model confidence is metadata only. Residual relevance limits are documented, not claimed away.
- Stale-citation correction: revalidate cited-version freshness, active status and authorization inside the draft-commit transaction under `FOR SHARE` row locks, so a source revised or archived during inference persists as unsupported instead of grounded.

## B4 decisions — owner approved 29 October 2026

- Keep owner-managed tasks in a new `followup_tasks` table rather than extending agent-run `followup_drafts`; run-bound drafts, approvals and their integrity triggers stay untouched.
- Run content edits before status validation so an edit while in_review returns to in_progress first: reviewed completion always reflects the latest revision, and content plus approval cannot combine in one step. A request that changes content on an in_review task and explicitly targets in_review or done is rejected (not silently demoted); resubmission and completion each need a separate versioned operation. Done is terminal with a CHECK-bound outcome; cancelled reopens to open.
- Record full backend-owned task snapshots (identity, relationships, content, due date, assignee, status, outcome, version) in before/after activity state, serialized with the shared encoder; older three-field snapshots stay readable and are never backfilled. Task creation share-locks its lead and property reads so concurrent archival or deactivation serializes instead of interleaving.
- Derive overdue at read from due date versus current time; reminders are data only with no scheduler, sender or delivery integration. No sent/delivered state exists at any layer; the BFF allowlist pins the absence of send/deliver paths.
- Treat tasks as internal owner work that ignores B1 opt-out/handoff without changing contact permission, while archived leads block mutations until restored. [Contract](phase-b-plan.md#b4-contract--approved-29-october-2026), [verification](phase-b4-verification.md).

## B5 decisions — owner approved 29 October 2026

- Record visit outcomes as terminal owner-asserted facts on confirmed bookings only; elapsed time never implies attendance. Outcomes are completed/no-show with an optional note; cancelled rows reject outcome writes at both API and CHECK level.
- Derive the five-state visit result at read (completed/no-show from the recorded outcome; rescheduled when another booking references a cancelled original; cancelled without a successor; otherwise scheduled). `replaces_booking_id` belongs to the replacement and points back; the current appointment is the confirmed booking no replacement points to.
- Guard cancellation and replacement against recorded outcomes in the shared booking lock path, so prepare/commit/cancel reject completed and no-show visits; a missed visit rebooks as a fresh request with no replacement link. Outcome-versus-cancel races serialize on the booking row lock with atomic rejection.
- Run visit follow-up (none/pending/done with reopen, required next-action notes, no done→none edge) independently of booking status and outcome; archived leads block writes until restored. No new journey stage is added and none auto-advances.
- No new registry tool, model authority, scheduler, notification, channel, delivery or bulk operation. [Contract](phase-b-plan.md#b5-contract--proposed-29-october-2026), [evidence](phase-b5-verification.md).

## Phase C decisions — owner approved 30 October 2026

- Create one isolated lead and conversation per chat start; never auto-associate a visitor with an existing lead. Email collisions (existing or archived, handled identically) use a transparent plus-suffixed stored alias with the asserted address verbatim in server-written notes, and every start response is indistinguishable, so no existence or archive oracle exists. Either address is never treated as verified and enables no delivery.
- Scope stateless HMAC visitor tokens (`v1.payload.mac`, 24h TTL) to exactly one conversation. Validation is strict on shape/types/size/lifetime with constant-time MAC comparison, authentication completes before any record lookup, and mint/verify fail closed without the server-only secret. Tokens authorize no other conversation, lead or owner endpoint; owner resolution freezes subsequent messaging and drafting as the revocation path.
- Derive the reply identity server-side as `chat:draft:{message_id}`: at most one assistant reply per persisted customer message across retries, concurrent sends and crash recovery. Replays consume no rate allowance and repeat no inference after a terminal result; chat-start replay returns the identical lead, conversation and token.
- Keep `draft()` the single grounding choke point with only truthful provenance threading (`actor` defaulting to `'owner'` on the shared mutate/save/append/draft/handoff/activity paths); owner behavior is byte-identical and clients choose no actor, role, lead binding or model authority. Visitor operations and messages carry `visitor` provenance via additive migration 0010 (CHECK widening only, no new tables).
- Cap each conversation at 30 customer messages per trailing hour; per-conversation limits only, with no chat-creation, global inference-spend, CAPTCHA or IP-reputation bound — this increment is local fictional-data work, never described as production-ready. Visit intent persists as ordinary messages with provisional availability only; visitors create no requests, approvals, bookings or consent. [Contract](phase-c-plan.md), [evidence](phase-c-verification.md).

## Phase C acceptance corrections — 30 October to 1 November 2026

- Enforce the visitor-write freeze under transaction locks: resolved rejects every visitor write; pending-owner/requested-handoff rejects sends before persistence; requirements and property context stay allowed outside resolved. Guards are visitor-only flags or defaulted shared parameters; owner paths unchanged. [Rulings](phase-c-plan.md#acceptance-review-corrections--30-october-2026), [evidence](phase-c-verification.md#acceptance-review-corrections--30-october-to-1-november-2026).
- Blocked sends persist nothing and run no inference; a state change between an authorized append and the draft commit fails with an explicit saved-message disclosure, and the UI refetches the thread on every mutation failure. Crash recovery for authorized appends is preserved.
- Bind property question context through a token-scoped operation accepting only a validated active property drawn from the lead's recomputed current matches; draft commit revalidates the retrieval-time scope. Historical citations and message bodies stay immutable.
- Serialize same-key drafts with a session-level advisory claim (bounded wait, re-check-then-replay, release on completion, failure or disconnect). One persisted reply per customer message; no inference on completed replay; exactly-once external inference not claimed.
- Execute migration-0010 CHECK/RLS/FK/rollback checks where rollback or read-only fixtures allow; fresh empty-database application stays in the dedicated-empty-database pattern, unexecuted without `TEST_DATABASE_URL`.

## Phase D autonomy decisions — contract revised after FAIL review, 1 November 2026

- Authorize policy-gated autonomy ONLY within [the revised Phase D contract](phase-d-plan.md): autonomous booking on full policy pass, exception approval by the owner, validated routine auto-messaging, and policy-driven follow-ups. This supersedes older always-owner-approved product expectations solely inside that contract's explicit gates (identity isolation, constrained responses, resource scheduling, durable delivery, consent/stop rechecks); every other product surface keeps existing owner-approval semantics.
- Isolate every new channel identity (`identity_links` observed/active/revoked/superseded); link only by explicit owner action with evidence; never route or share context on unverified detail match. Attested sender proves channel control, not lead ownership.
- Constrain AI responses structurally: model tone/glue plus backend-bound facts, server-rendered critical values, deterministic glue scan, one regeneration then fixed fallback; B3 assembly kept for FAQ answers. No unrestricted model prose may introduce business facts or commitments.
- Schedule per-resource (`resources` + per-resource EXCLUDE + conflict checks), backfilling existing rows to the default owner resource; site capacity stays a separate count constraint. Owner cannot override integrity rules (conflicts, isolation, archived records, stop).
- Deliver through the durable outbox with attempt leases, fencing, explicit unknown resolution and honest at-most-once-per-attempt guarantees; no exactly-once claim. Minimum dispatcher ships in D2; D3 adds scheduled policies.
- AGENTS.md is intentionally unchanged: its rules govern the coding agent's development actions, not the owner-authorized runtime autonomy defined here.

## Phase D second-review corrections — 1 November 2026

- The first Phase D revision failed a second independent review (10 findings). The contract now embodies construction-safe messaging (closed catalog repertoire, zero model free text in auto-sent payloads; arbitrary prose is owner-reviewed drafts only), staged/submitting/unknown delivery with owner-mediated unknown resolution and no exactly-once claim, one unified booking lock order with per-resource serialization and an exact buffer predicate, versioned v1/v2 approval snapshots with legacy readers and byte-identical replays, completed identity→conversation/link/token/suppression rules, a commit-defined authorization linearization point with cap/cooldown/unknown accounting, replayable stored-payload webhooks with per-status receipt identity, commit-ordered summary sequences with disclosed degradation, and a fully specified D1 (fields, defaults, window triple, two clocks, D1-owned transactional booking). [Revised contract](phase-d-plan.md).
- Bounded supersession restated: owner-authorized product-runtime autonomy operates strictly inside the contract's gates; development authorization requirements (single editor, explicit owner approval for external actions, no commit/push/deploy, no customer contact without authorization) are unchanged and are not weakened by any product-scope decision. AGENTS.md remains unedited by design.

## Phase D third-review corrections — 1 November 2026

- Delivery authorization is the COMMIT of the `staged → submitting` transition transaction, which rechecks every gate (consent, suppression, stop, link/destination, category, policy, reservation, cooldown) on current rows; staging alone reserves but never authorizes submission. Opt-out/stop/disable committing before the transition blocks it (attempt terminal `blocked`, reservation refunded); after it, no recall is possible and §10 unknown rules govern. Cooldown counts open attempts plus acknowledged sends; caps refund on blocked/abandoned/failed and hold on unknown; resends consume new reservations. [Contract §11](phase-d-plan.md).
- Legacy fingerprints use a deterministic v1 projection (D1-added optional keys stripped from `arguments` and `provided_fields`, following the existing `model_fields_set`/`legacy_digest` precedent in `CRM.mutate()`): stored-digest equality replays byte-identical responses; explicit non-null D1 values take a v2 digest path; mismatches without D1 values keep the different-operation rejection. Omitted and explicit-null D1 keys are equivalent (v1). No historical fingerprint or saved response is altered. [Contract §7](phase-d-plan.md).
- Commit re-evaluates current policy text under locks (notice recomputed at the commit instant); approvals authorize only their recorded overridable exception categories; integrity rules are never overridable; stored versions/hashes are not pass proofs. [Contract §8](phase-d-plan.md).
- Website canonical handoff is deferred: Phase C isolated-start/token semantics are preserved verbatim and linking never widens token scope. Booking and followup v2 snapshots are independent schemas. Webhook conflicts quarantine to `webhook_conflicts` without touching the original event. Multi-resource locks acquire in ascending-UUID order.

## Phase D fourth-review corrections — 1 November 2026

- Fingerprint selection is presence-first: `select_identity` inspects `model_fields_set`, and any explicitly provided non-null D1 key selects the v2 identity exclusively with no stripped-v1 fallback — an explicit `resource_id` (or channel/destination) against a stored v1 digest is a different-operation conflict with zero writes. Omitted/explicit-null D1 keys keep the v1 projection with all legacy rules intact; defaults never select v2; history is never rewritten. [Contract §7](phase-d-plan.md).
- Emergency stop blocks new autonomous booking commits and automated delivery, cancels nothing, and never gates direct owner CRM actions (existing authorization/safeguards unchanged). Owner exception approvals cannot bypass an active stop for autonomy — lift the stop or use the direct owner workflow. Autonomous commits serialize on `automation_controls` first; stop-blocked requests wait in `awaiting_owner` with reason `emergency_stop`. [Contract §§6, 8](phase-d-plan.md).
- The delivery authorization boundary is the COMMIT of the `staged → submitting` transition (full gate recheck in-transaction); staging reserves only. D3-LIM-04/05 now state this boundary. [Contract §§10, 11, 18](phase-d-plan.md).
