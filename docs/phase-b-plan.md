# Proposed Phase B: complete lead-to-appointment journey

The broader journey remains a proposal; the bounded B1 and owner-approved B2 contracts below describe the implemented foundation. Phase B builds on Phase A's shared records, consent and versioned operations; it must not create a parallel CRM.

1. **Journey and contact model.** Define explicit new → qualifying → qualified → matched → visit proposed → booked → visited/closed transitions, with reasoned exits for disqualified, opt-out and human handoff. Add contact preferences and a persistent activity history without conflating lead status, appointment status or consent. Record manual overrides and test invalid transitions.
2. **Qualification and matching.** Capture budget/locality/bedroom/timing requirements through owner-assisted flows first. Extend the existing typed registry to read/write only validated journey fields. Muse Spark may suggest questions and matches; deterministic criteria, consent and state transitions remain backend-owned.
3. **FAQ and conversation foundation.** Add an owner-managed, versioned property/business FAQ source and conversation/message records with citations, confidence/unsupported-answer handling and human handoff. Start with a local owner-reviewed simulation on fictional data. Do not treat a generated answer as delivered.
4. **Follow-up/task lifecycle.** Persist due dates, assignment, reminders, outcomes and history. Support draft revision and fresh review after content changes. Keep delivery states truthful. Background scheduling is a separately tested increment with explicit idempotency, retries and cancellation, not a hidden addition to request-based execution.
5. **Visit proposal to completion.** Connect matches and conversations to available slots, explicit proposal/consent, existing approved booking, cancellation and atomic replacement. Add attended/no-show outcomes through new validated transitions rather than inferring attendance from elapsed time.
6. **Owner policy controls.** Define per-agent capabilities, permitted channels/actions, approval thresholds, rate/spend limits, opt-outs, handoff and an emergency stop. Policies must be checked server-side at execution and delivery time. Emergency stop must prevent queued/retried actions as well as new ones. Model prompts alone are insufficient.
7. **Channel adapters, one increment at a time.** After the journey/policy foundation passes tests, propose website chat first and WhatsApp separately. Require explicit owner approval for account/credential setup and real external delivery. Verify webhook authentication, customer identity, consent/opt-out, deduplication and provider delivery receipts. Keep adapters behind interfaces so tests never contact real customers.
8. **Specialized sales/communication agents.** Introduce bounded roles only after the shared journey, policy and channel contracts exist. Reuse Muse Spark, typed tools, execution evidence and owner controls. Add multi-agent work only if it improves a defined journey task; prohibit approval or policy escalation by any model.

Acceptance evidence should follow one fictional lead through qualification, cited FAQ, matching, a reviewed follow-up, visit proposal, booking, reschedule/cancellation and human handoff/opt-out. Test retries, stale consent, competing visits, policy changes mid-run, emergency stop, missing knowledge, provider errors and truthful delivery reporting. External integrations remain separately authorized milestones.

## B1 contract

Authorized by the owner on 28 October 2026: customer journey and contact model only. This superseded the earlier narrower owner-qualification recommendation. B2 was subsequently approved separately on 29 October; its contract follows below.

Files: backend/app/journey.py (schemas, transitions, transactional writes, activity reads), backend/app/crm_api.py (owner routes), backend/app/tools/service.py and backend/app/owner.py (contact checks); supabase/migrations/0005_customer_journey.sql; frontend/src/components/lead-journey.tsx integrated into crm-records.tsx; the existing BFF route; focused backend/frontend tests and existing CRM browser fixture/workflow.

The migration adds four columns to leads and one private RLS table, lead_activities. Existing IDs, lead statuses, bookings, immutable approval snapshots and execution evidence remain intact. No synthetic history is created. Existing version/idempotency machinery records owner operations without AI execution steps.

Journey: new → qualifying → qualified. New/qualifying/qualified can move to disqualified or closed; qualified can return to qualifying; disqualified/closed can reopen to qualifying with reason. Stages matched, proposed, booked and visited wait for later workflows. Status qualified is an explicit owner assertion using existing required budget/locality/bedroom data, not automatic AI qualification.

Handoff: none → requested → in_progress → resolved; requested may resolve directly, and resolved may reopen to requested. It is separate from journey and opt-out.

Preference: unspecified/email/phone/none. It expresses intent only; no phone number capture or channel adapter is added. Permission: unknown/opted_in/opted_out. Opt-out cannot return to unknown; an owner must explicitly record opted_in with evidence/reason. All state writes require a nonblank reason, expected_version and request_key. Archived leads require restoration.

Opt-out, preference none or active handoff blocks new drafts, follow-up approvals, booking requests and booking execution. Rejection and cancellation remain possible; existing bookings/history are not erased. Unknown is not delivery consent. No delivery status is added because no delivery occurs.

Owner API additions:
- POST /owner/crm/leads/{id}/journey: validated partial changes plus reason, expected_version, request_key; returns committed lead.
- GET /owner/crm/leads/{id}/activities?page=1&page_size=20: paginated B1 history (maximum page size 100).
- Existing GET lead detail includes the new fields. Existing lead-edit API cannot write them.

Acceptance and tests: all journey/handoff edges validated, enum/extra-field rejection, owner authorization, persistence/reload, operation replay/conflict, stale versions, missing/archived records, activity failure rollback, blocked registered tools and previously approved actions, UI success/error handling, explicit BFF allowlist and real fictional CRM browser workflow. Run regressions; record exact executed results in HANDOFF, keeping historical Phase A evidence unchanged.

No channels, delivery, background jobs, conversations/FAQ, agent network, expanded model authority or new external integrations belong to B1. B1 stopped here; B2 was proposed and approved separately below.

## B2 contract — approved 29 October 2026

The owner reviewed and approved the B2 plan before editing. B2 implements owner-managed qualification and live deterministic matching only; B3 and later remain unstarted/unapproved. The broader future milestones above remain proposals. [B2 verification](phase-b2-verification.md) owns actual results and limitations; do not rewrite the B1 report as new evidence.

Reuse existing leads budget_min/budget_max/preferred_localities/bedrooms, allow genuine NULL answers, and add purchase_timeframe plus a server-managed qualification_fingerprint in migration 0006. Existing data/IDs, seed, approval snapshots, legacy lead status and booking evidence stay intact. No duplicate CRM/preferences or matching table.

Separate matching readiness (known core constraints) from qualification readiness (also known timeframe) and owner confirmation. Requirement changes invalidate current review; historical before/after activity remains on lead_activities and crm_operations. Only explicit owner review can establish current qualification, through the qualify endpoint or guarded generic journey transition. Unknown contact permission is not delivery consent; opt-out/handoff are not lifted by qualification.

API: existing GET lead detail gains qualification readiness; existing create/PATCH lead APIs support unknown answers and timeframe. PATCH omission preserves; explicit NULL clears nullable requirements. POST /owner/crm/leads/{id}/qualify requires reason/version/key. GET /owner/crm/leads/{id}/matches?limit=1..20 is owner-authenticated/read-only and shares the get_property_matches service. Models cannot mutate qualifications or choose arbitrary match filters.

Matching retains active inventory, inclusive budget range, normalized exact locality and minimum bedrooms; rank exact bedrooms, price, then ID. Explanations show requested/actual hard constraints and record versions/check time. Incomplete requirements differ from zero eligible properties. Scores are deterministic rankings, not confidence. Timeframe is not a possession-date filter.

Acceptance: persist/reload incomplete and completed answers on the same lead; reject invalid/unauthorized/stale/conflicting requests; protect audit atomicity and competing review/edit; make API/tool match results equivalent and stable; preserve all consent/booking regressions; verify real fictional UI create/edit/qualify/requalify/match plus existing visits. Record executed pass/skip/failure/warning results. Stop after B2.
