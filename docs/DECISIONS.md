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
