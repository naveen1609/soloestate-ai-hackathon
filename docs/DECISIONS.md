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
- No new model mutation tool, autonomous qualification, delivery, background worker or B3 feature. [Contract](phase-b-plan.md#b2-contract--approved-29-october-2026), [verification](phase-b2-verification.md).
