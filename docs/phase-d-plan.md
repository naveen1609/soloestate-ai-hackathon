# Phase D: autonomous channels and background automation — second revision after FAIL review, 1 November 2026

Status: **revised; awaiting independent review.** The second revision was independently reviewed a third time: substantially improved but **not yet ready for D1 authorization** (4 HIGH + 4 smaller findings below). This revision makes focused corrections only; all previously resolved decisions and matrices are preserved. Prior review history is retained in §1. Phase C is [PASS-closed](phase-c-verification.md#independent-acceptance-and-closure--codex-1-november-2026). Planning only: no implementation, commit, push, deployment, provider setup, credential creation, tunnel, delivery or customer contact. Implementation remains separately authorized. Owner product requirements are preserved except where a finding forces an explicit, documented tradeoff (§5 reconciliation).

## 1. Review history and finding matrices (normative)

### 1A. First review (FAIL) — retained history

First verdict: FAIL — 4 blockers (identity safety, safe messaging, resource scheduling, durable delivery), 5 high (capacity paths, approval lifecycle, consent/binding, authorization races, D2/D3 boundary), 3 medium (summaries, datetime, webhooks), 1 low (decisions/instructions). First revision delivered: `identity_links`, constrained segments with glue scan, per-resource scheduling, outbox attempts with leases, capacity inventory, exception lifecycle, recipient binding, linearization point, D2 minimum dispatcher, summary/datetime/webhook precision, DECISIONS autonomy section, D1/D2/D3 matrices (22/20/8 cases). The second review found that revision insufficient in the ten areas of §1B; the superseded elements are listed per section.

### 1B. Second review (FAIL) — corrections in this revision

| # | Finding | Resolved in |
| --- | --- | --- |
| 1 | Unsupported auto-sent prose (glue scan insufficient) | §5 closed repertoire; no free model text in auto payloads; matrices D2-MSG |
| 2 | Unsafe delivery recovery (auto-resume, vague guarantee) | §10 staged/submitting/unknown protocol; owner-mediated resend; matrices D2-DLV, D3-DLV |
| 3 | Lock/resource inconsistency + buffer semantics | §6 unified lock order, resource lock, exact buffer predicate; matrices D1-SCH |
| 4 | Legacy approval/operation compat | §7 versioned snapshots, legacy readers, inventory, replay rules; matrices D1-LEG |
| 5 | Identity→conversation gaps (link effects, tokens, revocation, suppression) | §4 link/conversation/token rules; §9 destination basis + suppression list; matrices D1-ID, D2-ID |
| 6 | Authorization boundary and limit accounting | §11 committed authorization, fencing, cap/cooldown/unknown accounting; matrices D3-LIM |
| 7 | Unreplayable webhooks | §14 stored validated payloads, receipt identity, conflict behavior, leases; matrices D2-WEB |
| 8 | Summary races and silent fallback | §12 per-conversation seq, no-skip protocol, disclosed degradation; matrices D2-SUM |
| 9 | D1 not independently implementable | §15 full field specs + decided defaults; §2 D1-service boundary; §8 triple + clocks; matrices D1 |
| 10 | Truthfulness of records | This history + §17/§18 + DECISIONS/PROJECT_STATE/ROADMAP/HANDOFF updates; status stays revised/awaiting review |

Superseded from the first revision: glue/question scanning and fact-block-cancels-prose (§5); same-attempt auto-resumption after `submitted` (§10); resource-first lock order and expand-both buffer prose (§6); unversioned snapshot widening (§7); NULL-verified-destination reply equality (§9); in-memory/age-based webhook handling remnants (§14); `(created_at,id)` summary boundary and silent fallback (§12); pending numeric defaults (§15).

### 1C. Third review (not yet ready for D1 authorization) — corrections in this revision

Third verdict: substantially improved; 4 HIGH + 4 smaller findings remain. All prior decisions and matrices preserved; only the affected sections below changed.

| # | Finding | Resolved in |
| --- | --- | --- |
| H1 | Authorization must be rechecked at the staged→submitting transition, not only at staging | §11 transition-time recheck + linearization; §10 crash/guarantee alignment; cases D3-LIM-06..12 |
| H2 | Legacy fingerprint compatibility needs the exact `mutate()` projection algorithm | §7 deterministic v1 projection + v1/v2 selection; cases D1-LEG-06..12 |
| H3 | Policy must be re-evaluated at commit under locks | §8 authoritative commit rule; §7 implements; cases D1-APR-08..14 |
| H4 | Website canonical tokens need the conservative option | §4 Phase C behavior preserved, canonical handoff deferred; cases D2-WWW-01..06 |
| S-A | Split booking vs followup v2 snapshot schemas | §7 independent schemas/readers/validators; cases D1-SNP-01..04 |
| S-B | Precise delivery guarantee + retry language | §10 guarantee rewrite (definitive-retry vs unknown path); D3 matrix language aligned |
| S-C | Deterministic multi-resource lock ordering | §6 total order + winner-take-all; cases D1-SCH-14..15 |
| S-D | Preserve original webhook event on payload conflict | §14 `webhook_conflicts` quarantine table (0012); cases D2-WEB-07..08 |

Superseded from the second revision: staging-commit linearization (§11); unspecified fingerprint projection (§7); request-time-only policy wording (§8); new-start canonical website tokens (§4); single merged v2 snapshot (§7); “exactly one attempt unless owner resend” phrasing (§10); candidate multi-locking auto-assign (§6); conflict-overwrite webhook status (§14).

### 1D. Fourth review (focused corrections) — in this revision

Fourth verdict: 1 HIGH defect + 2 documentation contradictions; prior architecture otherwise accepted. Focused corrections only; all previous decisions and cases preserved.

| # | Finding | Resolved in |
| --- | --- | --- |
| HIGH | Fingerprint v1-first selection wrongly replays over explicit D1 fields | §7 presence-first `select_identity` (explicit non-null D1 ⇒ v2 exclusively, no v1 fallback) + per-action D1 keys; corrected D1-LEG-08; new D1-LEG-13..16 |
| C1 | Emergency-stop booking contradiction (§8 both non-overridable and non-blocking) | §8 conservative rule (stop blocks autonomous commits, never cancels, never gates direct owner CRM; exception approvals cannot bypass; controls-first serialization; `awaiting_owner`+reason); §6 order note; new D1-APR-15..18 |
| C2 | D3-LIM-04/05 still name staging commit as the authorization boundary | Rewritten to the staged→submitting transition commit; §10/§11 already aligned; no other current normative reference names staging |

Superseded from the third revision: v1-digest-first selection order (§7); “stop does not block booking commits” sentence (§8); staging-commit wording in D3-LIM-04/05 (§18).

## 2. Increment boundaries (finding 9)

- **D1. Channel-independent transactional services + foundations (no adapters, no provider calls, no autonomous sending).** Delivers: business policies, resources + backfill, shared availability evaluator, `booking_requests` lifecycle with autonomous commit, identity-link schema + owner endpoints + isolation evaluator, consent + suppression schema, stop/limits storage, follow-up policy storage, message `seq`, delivery authorization evaluator + cap/cooldown reservation functions (pure + transactional, no provider). Driven by owner API plus a synthetic channel harness issuing fictional requests; every gate unit- and concurrency-tested. D1 commits policy-approved bookings itself — booking execution is D1's, not D2's.
- **D2. Channel invocation, constrained messaging, minimum durable delivery.** Invokes D1 services through adapters (WhatsApp webhook/Cloud API; website engine adapter over Phase C); closed-repertoire response pipeline (§5); outbox + attempts + minimum dispatcher (inline processing, explicit process-now, lease recovery, receipts, owner UI). D2 adds no new transactional booking semantics.
- **D3. Scheduled policies and operations, accurately bounded.** Reminder/nudge enqueue, delivery-time regeneration, retries/backoff/dead-letter ops, suppression handling, concurrency hardening. Invocation is explicit bounded entry points only; D3 is “policy-driven automatic sending upon processing” — per-message decisions automatic, cadence not claimed in-app. Automatic invocation stays a separately authorized deployment concern.

## 3. Architecture (kept)

Shared engine, thin adapters; reuse of CRM, conversations, approvals, bookings, operation evidence; no duplicate stores; no multi-agent architecture; no unrestricted model mutations; seven tool names preserved; single-owner isolation (multi-tenancy out of scope); additive migrations; website chat preserved. Agent orchestrator unchanged (`draft_first` = drafts + approval requests only). Autonomous execution is backend-owned entry points with gates and budgets.

## 4. Identity isolation, linking, and conversation completion (finding 5)

Attested sender proves channel control, never lead ownership. New identities start isolated; no automatic linking.

- **Binding table `identity_links`** (0011): `{id, channel ('website','whatsapp'), provider_ref (wa_id; website `web:<conversation_id>`), lead_id, verified_destination NULL|E.164|email, method NULL|'owner_verified', evidence NULL|jsonb, status observed|active|revoked|superseded, version, created_by_actor, created_at, decided_at}`. Partial unique `(channel, provider_ref)` where status ∈ (observed, active). Full fields in §15.
- **First inbound** (any channel): unknown identity → advisory lock + insert `observed` row bound to a fresh isolated lead + conversation (actor `channel`). Concurrent first-inbound: one winner, loser replays the winner's row (partial unique backstop).
- **Link action** (owner-only, evidence required, version-guarded): observed → `superseded` + new `active` row to the canonical lead with `verified_destination` set, atomically. Effects: isolated lead archived via the existing flow; isolated conversation UNCHANGED (same `lead_id`, history, status — no auto-resolve; owner may resolve via review); canonical conversation resolved-or-created: exactly one open canonical conversation per (lead, channel) — reuse it if present (return id), else create with `lead_id`=canonical. Later inbound routes to the canonical conversation. No history is ever moved or rewritten.
- **Website tokens, conservative** (third review finding 4): Phase C `chat.start()` behavior is preserved verbatim — every website start mints an isolated lead + conversation and a token scoped to exactly that conversation. No canonical-conversation token is issued on asserted email, phone, or knowledge of a conversation id; existing visitor access semantics are unchanged; no website visitor identity is silently merged into a CRM lead; no visitor history is moved or exposed by linking. Cross-channel identity linking happens ONLY through the owner-verified `identity_links` process; the website token flow itself never establishes canonical CRM identity. Authenticated canonical website handoff (a visitor proving ownership of a linked identity) is deferred to a separately designed future capability — and that future design must not retroactively widen any existing token's scope. Regression matrix D2-WWW pins all six behaviors.
- **Revoke/reassign**: `active` → `revoked` (reason required). Queued outbox rows bound to that link id fail delivery revalidation → `skipped_link_invalid` (owner-visible); in-flight attempts are never recalled (§10/§11); next inbound mints fresh `observed` (quarantine). Reassignment = revoke + activate in one transaction; queued rows under the old link id are NOT rebound — re-enqueue explicitly under the new link (audited).
- **Isolation enforcement**: engine resolves lead solely via the live link row; observed contexts see only isolated rows; canonical-history reads, non-thread outbound and bookings-against-history require `active`. `leads.phone` is a contact detail, never a routing key (non-unique).
- **Duplicate resolution**: owner UI pairs observed rows with canonical candidates; link/unlink/revoke/evidence only; merge excluded.
- D1: schema + endpoints + evaluator + synthetic-channel tests. D2: adapter wiring.

## 5. Closed response repertoire (finding 1)

Keyword/glue scanning cannot certify absence of unsupported propositions — so automatically sent payloads contain zero model-generated free text, by construction. Reconciliation with the owner's personalization requirement: the AI still composes every routine message (selects and orders content, fills references from live data), but every emitted token is either a backend-owned catalog string or a server-rendered validated fact. Arbitrary AI prose remains available exclusively as owner-reviewed drafts.

- **Catalog** (code-enumerated, versioned, e.g. `PHRASEBOOK_V1`): phrase ids (`ACK_*`, `TRANS_*`) and question ids (`Q_BUDGET`, `Q_LOCALITY`, `Q_TIME_PREF`, `Q_CONFIRM_WINDOW`, …), each with fixed text, typed slots (slots accept fact-registry ids only), category, and tone variants (2–3 per intent; model picks variant + order). New wording requires catalog change + review + version bump. No free-text fields exist in the auto-send output schema.
- **Model output contract** (strict JSON): `{catalog_version, category, segments: [{phrase_id|question_id, args: {slot: fact_ref}} | {fact_only: [fact_ref]} | {action_ref: committed_record_id}]}`. Unknown ids, unknown fields, or any `text` key ⇒ schema rejection.
- **Backend fact registry per turn**: `f1..fn` from fresh reads `{type, id, version, rendered, shareable}`. Validation: catalog version current; every id ∈ catalog; every `fact_ref` ∈ registry + row re-read current and shareable; slot types match; category recomputed by backend rules (sensitive markers ⇒ escalation regardless of label); assembly purely server-side (catalog strings + canonical fact renderings, fixed order).
- **Failure**: one regeneration with machine feedback (rejected ids/reasons), then fixed fallback clarification + escalation (`pending_owner` + owner draft where the turn needs one). Exactly one customer-visible message per inbound; no partial sends.
- **FAQ**: B3 quote-only assembly preserved — `faq_answer` renders validated quotes only, plus at most one catalog ACK prefix. No model prose in factual position, ever.
- **Prompt injection**: policy/booking/consent/authorization consume typed backend values only; retrieved free text, summaries, descriptions and customer messages are untrusted, wrapped in delimited blocks, and cannot add catalog ids (schema-enforced) or alter any gate outcome; injection fixtures assert unchanged outcomes + quarantine logging.
- **Persistence**: raw model output (marked unvalidated), selected ids + fact refs + row versions, rendered payload, decision trace. Grounding `'validated'` only on full pass. Adversarial matrix D2-MSG covers contradictory-prose-by-construction-absence, extra propositions, paraphrased promises, sold-as-available, injection, mutated values, fallback exactness.

## 6. Resources, unified locks, buffer rule (finding 3)

- **Model**: `resources {id, name, kind='person', active, version}`; every booking names one concrete resource (pools deferred). Migration seeds the default owner resource (fixed UUID) and backfills slots/bookings; ids, windows, history preserved.
- **Constraints**: per-resource slot EXCLUDE (`resource_id =`, window `&&`); partial unique `one_active_booking_per_slot_resource (slot_id, resource_id)` where confirmed; composite FK `bookings(slot_id, resource_id)`; `guard_booking` redefined (resource equality, zero-conflict count, binding match — §7); `guard_slot` redefined (add `resource_id` to the protected set: no delete/start/end/resource change while confirmed bookings reference the slot; `enabled` flips stay allowed and affect future reads only); `guard_booking` UPDATE branch adds `resource_id` to the immutable set.
- **One lock order for all paths** (owner, agent, autonomy, trigger): `run → approval → resource → slot(s) → property → lead`. This reconciles the existing run→approval→slot order by inserting the resource lock between approval and slot in `_booking_snapshot` and every caller; triggers re-lock in the same order (no-ops on held locks). The earlier resource-first instruction is withdrawn. Autonomy commit paths prefix `automation_controls →` for the serialized stop check (§8); direct owner paths take no controls lock.
- **Conflict predicate** (exact): for buffer `buf` minutes, bookings A,B conflict iff `(a.start < b.end+buf ∧ b.start−buf < a.end) ∨ (b.start < a.end+buf ∧ a.start−buf < b.end)`, half-open. Rationale: either core window may not intersect the other's buffered window; buffers may overlap each other. With buf=15 and a booking ending 11:30: an 11:30 start conflicts, an 11:45 start passes — the D1-SCH-07 example stands as written; the expand-both prose (which implied 30 min) is corrected to this predicate everywhere. Evaluation locks the resource row first, so bookings in different adjacent slot rows serialize on the shared resource lock.
- **Site capacity**: `properties.concurrent_visit_capacity NULL` (NULL = unconstrained); when set, booking counts overlapping confirmed bookings per property under the property lock (FOR UPDATE, uniform in all paths) and requires count < capacity.
- **Assignment**: explicit resource ⇒ lock resource + slot, verify, hold through insert. Auto-assign ⇒ evaluate candidates lock-free, then lock the single winner (resource + slot) and re-verify; on loss, retry the next candidate (bounded). Resource deactivation: owner action locks resource, rejects if future confirmed bookings exist (lists them; owner reassigns/cancels first), then deactivates — same protocol.
- **Multi-resource ordering** (third review 5C — deterministic total order): whenever a transaction must hold more than one resource lock (reassignment flows, bulk checks), sort resource UUIDs ascending and acquire strictly in that order, before any slot lock; the same order governs initial bookings, replacements, cancellation-driven frees and deactivation checks. Common paths hold exactly one resource lock via winner-take-all verification above; the total order exists for flows needing several and any future bulk path. Overlapping resource sets therefore serialize identically — no deadlock cycle is possible. Matrix cases D1-SCH-14/15 prove concurrent resource-swapping replacements and multi-resource acquisition complete without deadlock or double-booking.
- **Unavailability after request**: commit revalidation fails ⇒ `awaiting_owner` + reason + fresh alternatives, or expiry; never books onto an inactive/conflicting resource. Replacement/cancel/B5 mechanics preserved (§7).

## 7. Capacity paths, versioned snapshots, legacy compatibility (finding 4)

Current approvals bind immutable email snapshots with exact equality and stable fingerprints. D1 extends without rewriting history.

- **Snapshot versions, split by kind** (third review 5A — booking and followup are independent schemas): v1 = current shape (no version key ⇒ v1). Booking v1: `{lead_id, recipient=email, property_id, property_title, price, slot_id, starts_at, ends_at}`. Followup v1: `{draft_id, lead_id, version, channel=email, recipient=email, subject, body}`. Booking v2 adds `{snapshot_version: 2, resource_id, buffer_min}` only — no channel/destination fields (a booking commits no message; recipient email stays as lead corroboration). Followup v2 is `{snapshot_version: 2, draft_id, lead_id, version, channel, verified_destination, identity_link_id, destination_basis, subject, body, category, policy_version, consent{permission, source, time}}` — never resource/duration/buffer fields. Independent readers, validators and serializers per (kind, version); cross-domain fields are rejected (`invalid_arguments`). Policy version is deliberately NOT in booking snapshots (policy gates request/decision; commit enforces integrity + binding per §8).
- **Readers/comparators**: version-dispatched readers return (version, normalized); v1 comparator = legacy exact equality on v1 keys; v2 comparator = exact equality on v2 keys. `payload_hash` (canonical JSON sha256) unchanged; stored `crm_operations` responses and `run_steps` evidence replay byte-identical — never recomputed, never rewritten. Historical snapshots, decisions, responses and identifiers are immutable.
- **Fingerprint version-selection algorithm** (fourth review HIGH 1 — presence-first; the third-review v1-first order was wrong because it replayed stripped digests over explicit D1 selections): `select_identity(action, request)`: (A) inspect `request.model_fields_set` (field presence, never the defaults-included dump): D1-SELECTED iff any per-action D1 key below is both present in `model_fields_set` AND non-None. Defaults (e.g. an unprovided `channel="email"`) never select v2. (B) If D1-SELECTED ⇒ compute ONLY `v2digest = payload_hash({action, arguments: full dump, provided_fields: sorted(fields_set), schema_version: 2})`; compare exclusively against v2 digests — equality replays stored bytes, any mismatch against an existing key (v1 or v2 row) is a different-operation conflict with zero writes; stripped-v1 fallback is FORBIDDEN on this path. (C) If not D1-selected ⇒ compute the v1 projection (arguments restricted to per-action `V1KEYS` = the exact pre-D serialized key set, D1 keys stripped from `arguments` and `provided_fields`) plus the UNCHANGED legacy rules (`LeadCreate` pop, `provided_fields` binding, guarded `legacy_digest` fallback with its record-shape check); equality (or guarded legacy equality) replays, else different-operation rejection. Omitted and explicit-null D1 keys are equivalent (both v1). (E) New operations store the digest of their selected version; historical fingerprints and saved responses are never altered; v1 rows match only v1-selected requests and v2 rows only v2-selected requests (cross-version equality is impossible by construction — the `schema_version` key differs). (F) Replay returns stored bytes without executing business writes; changed-payload reuse is rejected exactly as today. The `crm:{key}` advisory lock, IntegrityError mapping and `mutate()` structure are unchanged; the actor allowlist widens `('owner','visitor')` → `('owner','visitor','channel','policy')` alongside the CHECK widening.
- **Per-action D1 keys** (exhaustive for v1-compat actions): `slot:create` → `{resource_id?}`; `appointment:prepare` → `{resource_id?}`; `appointment:commit:*` → none (identity keys unchanged; the hash value covers v2 snapshot content); every other pre-D action → none (pure v1 path, byte-identical behavior). New D1/D2 actions (`booking_requests:*`, `identity_links:*`, `policies:*`, `suppression:*`, `outbox:*`) are v2-only namespaces with no legacy rows. Projection rule: v1 strips exactly the tabled D1 keys for that action (plus existing special cases); serialization is `payload_hash` canonical JSON in all branches.
- **Legacy treatment**: pending v1 ⇒ decidable via the legacy decide path (v1 recompute + default-owner resource resolution + email binding); approved v1 ⇒ bookable (resource resolves to the default owner resource deterministically, recorded on the booking + audit); consumed v1 ⇒ terminal, untouched. v1 replays resolve the owner resource (documented compat rule); new requests with omitted `resource_id` auto-assign first-free (documented new rule) — the two defaults never mix.
- **Optional fields (backward compatible)**: `resource_id?` (absent ⇒ auto-assign/recorded), `channel?` (default `email`), `destination?` (required iff channel ≠ email). Required-field behavior and all seven tool names/semantics otherwise unchanged; registry descriptions updated only where behavior measurably changes (slots output gains `resource_id`; draft description gains whatsapp variant).
- **Inventory** (schemas/handlers/triggers/callers): `tools/schemas.py` (GetSlots/Approval/Book inputs+outputs), `tools/service.py` (get_available_slots, request_approval, book_site_visit, _booking_snapshot), `owner.py` (review/decide + v1/v2 recompute), `crm.py` (prepare/commit/cancel/reject/_booking + slot create with optional resource + generation), `visits.py` (untouched), `crm_schemas.py` (SlotCreate + resource?, AppointmentRequest/Commit passthrough), triggers (`guard_approval` messages, `guard_booking` redefine, `guard_slot` redefine, `guard_draft` channel widen only), routes (`/visit-slots`, `/appointment-requests/*`, `/appointments/*`, `/operations/{key}`, `/summary`), BFF allowlist (additive paths only), frontend appointment forms/fixtures/specs (resource select defaults owner).
- **Path inventory** (from first revision, kept): get_available_slots per-resource exclusion + filter; snapshot gains resource/conflict/buffer/binding; replay identity gains resource; stale_approval covers all drift; decide keeps run→approval→slot order with resource inserted; cancel/replacement/B5 mechanics unchanged.

## 8. Approval exception lifecycle

- **States** (`booking_requests`): `proposed → approved → booked`; `proposed → awaiting_owner → approved | denied | expired`; `approved → awaiting_owner` (commit revalidation failed) `| booked`; terminal `booked|denied|expired|cancelled`. Version-guarded, idempotent per request key, fully audited.
- **Window triple** (finding 9): `requested_*` (lead-stated/extracted) vs `customer_confirmed_*` (lead-confirmed exact window + resource; exact-grid requests adopt automatically and record the adoption; normalized windows require explicit lead confirmation per §13) vs `owner_approved_*` (owner-set on exception approval, may substitute alternatives). Auto path books `customer_confirmed`; exception path books `owner_approved`; commit binds and revalidates whichever governs.
- **Overridable (policy)**: hours/day/holiday/notice/duration/site-soft-limits/alternative substitution. **Non-overridable (integrity)**: resource conflicts, archived/inactive records, isolation violations, snapshot mismatch, authorization failure, emergency stop (blocks new autonomous commits and automated delivery until lifted; no approval bypasses it for autonomy — §8 commit rule). Owner cannot approve past integrity failures (endpoint rejects with the reason).
- **Two clocks** (finding 9): proposal TTL (30 min) governs lead confirmation of normalized windows (`proposed` + `proposal_expires_at`; lapse ⇒ `expired/proposal_lapsed`, re-request creates a new row); request expiry (`expires_at` = min(requested/confirmed start, created+72h)) governs the owner decision window for `awaiting_owner`. Owner alternative approval sets `owner_approved_*` + reason; the lead is notified of the substitution with server-rendered details before commit.
- **Binding/expiry/commit**: request hash over (governing window, property, resource, duration, policy version, snapshot hashes); decision requires `expected_request_hash`; commit recomputes (stale ⇒ `awaiting_owner` + fresh alternatives). D approvals are created+decided+consumed atomically at commit; legacy pending keeps current semantics.
- **Commit rule, authoritative** (third review finding 3 — §7 snapshot mechanics implement this rule; both sections describe the same transition): the request is evaluated under the policy current at request time. An owner approval records the requested and approved windows/resources, the policy context (version + evaluated-at instant + notice computed then), the EXPLICITLY allowed exception categories, and the immutable request binding. At commit, under the §6 locks, the backend evaluates the CURRENT policy text again: the 16h notice is recomputed from the actual commit instant (`start_utc − commit_utc ≥ 16h`, UTC elapsed), hours/day/holiday/duration rechecked, and integrity revalidated atomically (resource conflict, site capacity, archived/inactive, isolation, binding equality). A stored version or hash alone never proves current policy passes. The approval authorizes ONLY its recorded overridable categories: a gate failing on a recorded category passes; failing on any other category ⇒ stale ⇒ back to `awaiting_owner` + fresh alternatives + new owner decision required. Integrity failures deny unconditionally — approvals cannot override them. Policy text changed since approval ⇒ evaluated as current (same category-subset rule). Expired ⇒ terminal. **Emergency stop, conservative rule** (fourth review contradiction 2 — replaces the prior “stop does not block booking commits” sentence): an active stop BLOCKS new autonomous booking commits and all automated delivery, but cancels no existing booking. Direct owner CRM actions (prepare/commit/cancel/reject/outcome via the owner workflow) keep their existing authorization and safeguards untouched — stop is not a blanket owner-CRM shutdown. An owner exception approval cannot bypass an active stop for an autonomous commit: the commit refuses even with a valid exception approval; the owner must explicitly lift the stop (one audited action) or book through the direct owner workflow. Serialization: the autonomous commit path locks `automation_controls FOR UPDATE` FIRST (autonomy order: controls → run → approval → resource → slot → property → lead; §6); the stop toggle locks only controls (no cycle); direct owner paths take no controls lock and never consult the flag. A commit attempt blocked by stop moves the request to `awaiting_owner` with reason `emergency_stop` (from `proposed`, or back from `approved`), writes no approval/booking rows, and audits truthfully; re-attempt after lifting runs full revalidation. Reminder enqueue under stop is held as `skipped_stopped` (owner re-enqueues after lifting).

## 9. Consent, recipient binding, suppression (finding 5C)

- **Snapshot binding** (all outbound): `{channel, verified_destination, identity_link_id, destination_basis ('link'|'in_thread'), destination, lead_id, conversation_id?, category, content_ref, policy_version, consent{permission, source, time}}`. Email compat per §7.
- **Destination basis** (resolves the NULL conflict): `link` basis requires the link row `active` AND `destination == verified_destination`. `in_thread` basis (observed contexts only) requires the observed row still live (not superseded/revoked) AND within 24h of the thread's last inbound AND routine category AND thread match AND `destination == provider_ref` normalized. No equality against NULL anywhere.
- **Inbound evidence binding** (server-owned ingestion contract, no new table): every authenticated-ingress operation response carries `inbound_evidence = {channel, provider_ref, link_id}`, stamped server-side at ingress — website chat appends (handle pinned to the token's conversation, live website link id or null) and D2 webhooks (provider-attested sender, live link id after observe()). The `in_thread` window opens only on customer-role rows whose operation actor matches the channel (visitor/website, channel/whatsapp) AND whose envelope exactly matches the requested channel, attested sender, and live link generation on the claimed conversation. Cross-channel rows, cross-sender rows, stale-generation rows, and envelope-less rows qualify nothing; missing or ambiguous evidence fails closed. Provider event time (`provider_ts`) keeps its existing top-level envelope semantics for delayed-event refusal.
- **Delivery revalidation** (every send): basis check + permission current + B1 (opted_out/preference-none/handoff blocks all channels) + archived-lead block + suppression check FIRST (below). Any block ⇒ refuse with reason; stale link (revoked/superseded since enqueue) ⇒ `skipped_link_invalid`.
- **Suppression list** (durable, survives identity churn): `suppression_list {channel, destination UNIQUE, source, evidence_ref, created_at}`. Inbound exact-match opt-out keywords (`STOP STOPALL UNSUBSCRIBE CANCEL END QUIT`, English; extensions deferred) write suppression + set lead permission `opted_out` (source `inbound_keyword`, message evidence) and send NO reply — no confirmation, no exception. Delivery refuses on suppression hit regardless of permission. Re-opt-in: single owner-recorded action with evidence removes suppression + sets permission (audited). `whatsapp_permission` remains lead-level relationship state; suppression is the destination-level safety net.
- Owner-only internal notices are never customer delivery and never touch provider paths.

## 10. Durable delivery states and recovery (finding 2)

- **Identities**: outbox row id = logical message identity; stable key `outbox:{channel}:{purpose}:{source_id}` — retries never mint new keys to bypass uncertainty. `delivery_attempts {id, outbox_id, attempt_no, lease_owner, lease_until, fencing, provider_msg_id?, submitted_at, outcome}`.
- **States**: outbox `queued → staged → submitting → sent → delivered → read`, plus `failed` (definitive rejection), `unknown` (may-have-submitted, outcome unrecorded), `dead` (exhausted), `skipped_*` (reason-coded). Attempts mirror `staged → submitting → acknowledged | failed | unknown`. Partial unique: at most one open (`staged`/`submitting`/`unknown`) attempt per outbox row.
- **Pre-submission vs potentially-submitted**: `staged` = committed authorization, provider call provably never made under this attempt (protocol invariant: the call happens only after the staged→submitting transition commits). `submitting` = call may be in flight. Crash/timeout in `staged` (lease/validity expired, transition never recorded) ⇒ safe automatic re-drive of the SAME attempt row (new lease/fencing, same id). Crash/timeout in `submitting` without outcome ⇒ `unknown`, owner-mediated ONLY — no automatic resumption, ever. An expired lease or missing provider id proves nothing; anything past `staged` is treated as potentially submitted.
- **Submitting entry**: exactly one worker may commit the staged→submitting conditional update, which performs the full §11 gate recheck in the same transaction (on attempt id + fencing + valid lease + outbox still authorized + all gates current); losers abort. Fencing serializes transitions; it is NOT claimed to stop an already-running HTTP call — instead the protocol never requires that: at most one transition succeeds per attempt, and any worker observing `submitting` backs off to unknown-recovery rather than resubmitting. Same-attempt double submission is structurally impossible.
- **Unknown recovery**: await receipts within the bounded window (late receipts advance `sent` normally); an `unknown` lacking provider id cannot auto-match — unlinked receipts are logged + surfaced for owner matching, never auto-applied. Owner resolves each unknown: mark resolved-sent (evidence note) or authorize resend (new attempt row, same outbox id, duplicate risk disclosed in the UI + audit) or abandon (terminal). Sweeper reclaims expired leases into `unknown`, never into resend.
- **Crash points** (each with a matrix case): before staging commit (nothing exists; safe retry); staged + worker lost pre-transition (auto re-drive the SAME attempt id — provably never submitted); gate fails at transition (attempt terminal `blocked`, outbox to `queued`, reservation refunded); transition committed then crash pre-call (conservatively `unknown` — call unproven but possible); post-accept pre-ack (unknown); ack persisted pre-state-transition (idempotent replay); receipt application (provider-id idempotency).
- **Guarantee (exact)**: each attempt submits at most once. Per outbox row: exactly one submission in the crash-free path. Additional attempts arise only via (a) bounded policy retries after DEFINITIVE failures (failed/dead path, new attempt rows, same outbox id — these never passed through `unknown`), or (b) explicit owner-authorized resend after `unknown`, each authorization disclosing duplicate risk. Same-attempt re-drive is permitted only in `staged`, where the protocol proves no submission occurred. Potentially-submitted attempts are never automatically retried. Exactly-once delivery is NOT claimed.

## 11. Authorization boundary and limit accounting (third review finding 1)

Staging is reservation, not submission authorization. A ten-minute staged validity must not let a mid-window opt-out, stop or policy disable be ignored before any provider call starts.

- **Explicit states**: `staged` = gates passed at staging, caps/cooldown reserved, provider call provably never made under this attempt; `submitting` = the transition below committed, provider call may be in flight; `unknown` = may-have-submitted without recorded outcome. Only `staged` attempts are pre-submission.
- **Delivery authorization linearization point**: the COMMIT of the `staged → submitting` transition transaction. That transaction re-locks (outbox row → identity_link → lead → controls → policy → caps, same order as staging) and rechecks every gate on current rows: consent, suppression/opt-out, emergency stop, destination basis + link validity, message-category permission, policy enablement, reservation ownership + validity window, cooldown including open attempts. All pass + fencing/lease/state predicate holds ⇒ transition commits ⇒ the worker may submit. Any gate fails ⇒ attempt terminal `blocked`, outbox returns to `queued` (re-staging later is a new authorization, never a resumption of uncertainty — nothing was submitted).
- **Before/after split**: opt-out, stop, policy disable, link change or suppression committing BEFORE the transition commits ⇒ the recheck sees it ⇒ blocked. Committing AFTER ⇒ the provider request may already be in flight — no recall is claimed or attempted; the attempt follows §10 outcome rules and subsequent attempts skip.
- **Crash/timeout after the transition** follows §10 `unknown` rules (owner-mediated, never auto-resumed).
- **Concurrency**: the conditional transition (attempt id + fencing + valid lease + `staged` state + full recheck) admits exactly one worker per attempt; losers observe a non-`staged` attempt and abort. The provider call is reachable only after the winner's transition commits, so no worker can bypass the recheck.
- **Reservation accounting** (exact): staging increments caps and records cooldown against this attempt. Blocked at transition ⇒ refund. Abandoned ⇒ refund. Definitive failure/`dead` ⇒ refund. `unknown` ⇒ HOLD until resolved. Resolved-sent ⇒ keep. Owner-authorized resend ⇒ consume a NEW reservation. Cooldown counts open attempts (`staged`/`submitting`/`unknown`) plus acknowledged sends in-window, under the lead lock.
- **Races** (matrices D3-LIM): concurrent staging and concurrent transition (one winner each); cap-boundary exactness; cooldown with open attempts; opt-out/stop/disable before staging, between staging and transition, and after transition (ordered cases); in-flight non-recall with truthful post-hoc states.

## 12. Summaries with per-conversation sequence (finding 8)

`(created_at, id)` ordering can skip a message whose row commits behind an established boundary (clock skew across sessions: a later-committing row may carry an earlier timestamp). The boundary must be commit-ordered.

- **`conversation_messages.seq BIGINT NOT NULL`**, `UNIQUE(conversation_id, seq)`, assigned max+1 under the conversation `FOR UPDATE` lock at every append — matching the existing append/draft-commit lock discipline (`append()` and draft commit already hold the conversation lock when inserting). Backfill in 0011 orders by `(created_at, id)` per conversation. Any message committed later necessarily holds a higher seq: the lock serializes appends against boundary establishment, so no committed message can hide behind a boundary.
- **Boundary**: `through_seq` on append-only `conversation_summaries {conversation_id, through_seq, body ≤2000, facts ≤4KB jsonb, request_key}`; retrieval = latest summary + every `seq > through_seq` ordered by seq, up to 30 messages / 12KB. Same-conversation validation on `conversation_id`.
- **Summarize-before-truncate**: overflow triggers synchronous bounded regeneration first; tail truncates to last 10 / 8KB only after. Concurrent generation: conversation lock + idempotent replay on identical `through_seq`.
- **Degraded fallback (disclosed, never silent)**: summary failure ⇒ tail-only context WITH a `context_degraded` flag persisted on the turn record and shown as an owner-visible badge. Degraded context ⇒ pre-tail facts treated as unknown ⇒ clarify or escalate; preferences are never sourced from summaries (lead row re-read); missing context never authorizes actions and never invents customer requirements. The earlier “no customer-visible impact” claim is withdrawn.
- **Trust**: summaries stay untrusted context — never inputs to policy, authorization, identity, booking or consent. Retention append-only.

## 13. Date/time normalization

Business tz `Asia/Kolkata` default (IANA stored, owner-configurable); UTC storage; customer times interpreted in business tz unless an explicit offset is present. 16h notice on UTC instants. Working checks business-local. DST: ambiguous takes earlier occurrence, nonexistent shifts forward — both shown for confirmation (IST unaffected). Half-open overlaps + buffer predicate per §6. Duration default 30m, allowed 15–120m; out-of-range ⇒ exception. “Next two days” = today + tomorrow business-local (`start ≥ now+16h`, `start < start-of-day-after-tomorrow`), provisional with check time. Arbitrary times: normalize start to the 5-minute grid, propose the interpreted window + alternatives; booking requires lead confirmation of the exact window (bound to `booking_request` id, 30-min proposal TTL). No silent rounding or duration change.

## 14. Replayable webhooks (finding 7)

A payload hash cannot re-drive an event. Durable validated payloads can.

- Verification unchanged: raw-body HMAC-SHA256 (constant time) → account/phone-number-id match → shape + size validation (256KB payload bound; oversize/malformed ⇒ 400/401, nothing persisted).
- **`webhook_events {provider_event_id UNIQUE, channel, event_kind (message|receipt), provider_msg_id, status_key NULL|(sent|delivered|read|failed), received_at (authenticated delivery time), event_time, payload jsonb (validated as received), payload_hash, status received|claimed|processed|dead|rejected|conflict, lease_owner?, lease_until?, attempts}`**. The stored validated payload IS the replay input: processing = deterministic projection + handler, pure over (stored payload, current rows). PII lives only in this private table (same as conversation bodies); bodies never enter logs.
- **Event identity**: inbound messages keyed by provider wamid; receipts keyed by `(wamid, status)` — sent/delivered/read sharing one wamid are three distinct events, never collapsed.
- **Duplicates/conflicts** (third review 5D — original preserved): same id + same hash ⇒ idempotent replay of the stored outcome. Same id + different hash ⇒ the ORIGINAL row keeps its exact state (retries and recovery proceed unaffected) and the conflicting payload is appended to `webhook_conflicts {id, provider_event_id FK, payload, payload_hash, received_at}` (0012; first 10 per event kept, further arrivals increment a counter) for quarantine/audit, owner-visible. Conflicting payloads can never overwrite, re-drive, or corrupt the original event's processing.
- **Multi-entry**: validate each entry; persist all valid; single 200 after the batch is durable; invalid entries recorded as `rejected` with reason (bounded). Per-event async processing under lease (5 min, ≤3 attempts → `dead` + owner-visible). Delayed events process normally; business TTLs evaluate on `received_at`. No age-based rejection.

## 15. D1 field specs and decided defaults (finding 9)

Complete D1 schema surface (0011). All new tables private RLS, no browser policies; money/times follow existing conventions (numeric(14,2), timestamptz UTC).

- **`business_policies`** (single active row, owner-only, versioned): `timezone TEXT IANA default 'Asia/Kolkata'`; `working_days INT[] default {0,1,3,4,5,6}` (PG 0=Sunday..6=Saturday; Tuesday=2 excluded); `weekly_holiday INT default 2`; `hours_start/hours_end TIME default 10:00/17:00` (CHECK end>start); `holiday_dates DATE[] default {}`; `notice_hours INT default 16 CHECK >0`; `default_duration_min INT default 30`; `min_duration_min/max_duration_min INT default 15/120`; `buffer_minutes INT default 0 CHECK ≥0`; `version`, `updated_at`. Validation: end fits in hours; durations positive.
- **`resources`**: `{id, name TEXT nonblank, kind TEXT CHECK='person', active BOOL default true, version}`. Pools deferred (no pool rows accepted in D1).
- **`booking_requests`**: `{id, lead_id, property_id NULL, conversation_id NULL, channel, requested_start/ requested_end, requested_resource_id NULL, requested_duration_min, customer_confirmed_start/end/resource NULL, proposal_expires_at NULL, owner_approved_start/end/resource NULL, needs_owner_approval BOOL, exception_reasons TEXT[], status, approval_id NULL, booking_id NULL, policy_version INT, request_hash TEXT, expires_at, version}`. Requested vs confirmed vs approved per §8; two clocks per §8.
- **`identity_links`**: `{id, channel, provider_ref TEXT, lead_id, verified_destination NULL, method NULL|'owner_verified', evidence NULL|jsonb, status, version, created_by_actor, created_at, decided_at}` + partial unique live index (§4).
- **`suppression_list`**: `{channel, destination UNIQUE, source ('inbound_keyword'|'owner_action'), evidence_ref, created_at}`. Purpose: destination-level opt-out that survives link churn (§9).
- **`automation_policies`**: `{kind ('booking_reminder'|'followup_nudge'), hours_before NULL|INT, days_after NULL|INT, after_event NULL|TEXT, frequency TEXT, categories TEXT[], lead_statuses TEXT[], max_attempts INT, per_lead_cooldown_hours INT default 24, wording ('template'|'assisted'), enabled BOOL default false, version}`. D1 stores/validates only.
- **`automation_controls`**: single row `{id CHECK-pinned, emergency_stop BOOL default false, updated_at}`.
- **`delivery_caps`**: `{day DATE, channel, used INT default 0, cap INT}` (unique day+channel). 0011 (D1 needs reservation semantics testable); rows seeded/updated by the reservation function.
- **Consent columns** (`leads`): `phone TEXT NULL` (E.164 validated when present, non-unique — never a routing key); `whatsapp_permission TEXT default 'unknown' CHECK(unknown|opted_in|opted_out)`; changes via owner endpoints or inbound-keyword handler, always with source+evidence in activities.
- **`conversation_messages.seq`** (BIGSERIAL-like per conversation): BIGINT NOT NULL + UNIQUE(conversation_id, seq) + backfill (§12).

**Decided numeric defaults** (values, not architecture — the owner may adjust values pre-implementation without structural re-review): notice 16h; duration default 30 (15–120); buffer 0; per-day cap 25; per-lead cooldown 24h; per-thread auto 3/hour; proposal TTL 30 min; request expiry min(start, created+72h); staged validity 10 min; attempt/submit lease 5 min; webhook lease 5 min; backoff 5m/1h/6h, max 3; sweeper stuck 10 min, ≤3 drives; webhook payload 256KB; summary bound 30msg/12KB, tail 10/8KB, summary 2KB, facts 4KB; opt-out keywords `STOP STOPALL UNSUBSCRIBE CANCEL END QUIT`; receipt window 24h for unknown matching attempts.

## 16. Migrations (additive; 0001–0010 untouched)

- `0011_policy_resources_identity.sql` (D1): `business_policies` (+ seeded default row), `resources` (+ deterministic owner seed), `booking_requests`, `automation_policies`, `automation_controls` (stop=false), `identity_links` (+ partial unique), `suppression_list`, `delivery_caps`; `visit_slots.resource_id` + per-resource EXCLUDE (drop global) + backfill; `bookings.resource_id` + backfill + `one_active_booking_per_slot_resource` (drop old index) + composite FK; `guard_booking` + `guard_slot` redefined; `runs.origin` += `'autonomy'`; `approval_decisions.actor` += `'policy'` (+ accurate messages); `leads.phone` + `whatsapp_permission`; actors += `'channel','policy'`; `properties.concurrent_visit_capacity NULL`; `conversation_messages.seq` + backfill + unique. Legacy rows satisfy every constraint; stale “one owner” comments in old files stay as history.
- `0012_channel_delivery.sql` (D2): `conversations.channel` (nullable), `conversation_summaries`, `delivery_outbox`, `delivery_attempts`, `webhook_events`, `webhook_conflicts`, `followup_drafts.channel` += `'whatsapp'`; `grounding` += `'validated'` (`validated ⇒ citations empty`; FAQ claims keep `grounded`). D3 adds no tables.
- Each carries the 0010-style split: dedicated-pattern empty apply, CHECK/third-value, legacy validity (identical windows/counts, owner-resource backfill), FK chains, rollback, RLS/PUBLIC.
- D1 evidence note (2 November 2026, no normative change): authenticated-inbound thread evidence rides existing state — `conversation_messages` joined to `crm_operations` by request key, provider event time carried in the ingestion response envelope — so D1 needs no new migration and none was added. `0012_channel_delivery.sql` remains reserved for D2 exactly as specified above; no renumbering.

## 17. API, services, and instruction scope

- Modules (names adjustable): `policies.py` (CRUD/evaluation/generation/shared availability), `booking_requests.py` (lifecycle/commit), `resources.py`, `identity_links.py`, `channel.py` (interface + engine), `whatsapp.py` + provider routes, `respond.py` (closed-repertoire pipeline), `summaries.py`, `outbox.py` (staging/submit/receipts/recovery), `caps.py` (reservation/accounting), `services/whatsapp.py`.
- Owner routes (existing auth + additive BFF paths): policies, resources, slots/generation, booking-request list/decide, identity-link list/link/revoke, suppression list/remove-via-reoptin, outbox list/requeue/resolve-unknown, stop toggle, phone/consent edit. Provider webhook (HMAC only). No new registry tools.
- **AGENTS.md unchanged.** Standing development rules (single editor, owner authorization for external actions, no commit/push/deploy, no customer contact without explicit authorization) govern how software is built here and are NOT weakened. The product-runtime autonomy in this contract is owner-authorized product behavior operating strictly inside its gates; that bounded product-scope supersession is recorded in `docs/DECISIONS.md`, never as an edit to development instructions. D1 architecture is fully closed; D2/D3 externals (verification, templates, deployment cadence) are dependencies, not open design.

## 18. Acceptance matrices (revised; supersede first-revision matrices)

Format: scenario / initial → action → outcome / persisted + audit evidence / type. Every case asserts exact rows and no silent side effects.

### D1 matrix — scheduling, lifecycle, identity, policy, legacy, migration

- D1-SCH-01 pass / Wed 11:00 IST, 48h notice → evaluate → pass / nothing (pure function) / unit
- D1-SCH-02 Tuesday blocks / Tue 11:00 → fail `weekly_holiday` + exception path / nothing / unit
- D1-SCH-03 short notice / 10h → fail `short_notice` / nothing / unit
- D1-SCH-04 hours edge / 16:45+30m → fail; 16:30+30m → pass / nothing / unit
- D1-SCH-05 same-resource race / owner booked 11:00–11:30 → concurrent second booking same resource+window → exactly one wins, loser `resource_conflict`, zero partial rows / one booking + both ops / database concurrency
- D1-SCH-06 cross-resource overlap / owner booked 11:00 → agent-2 same window → succeeds / two bookings distinct resources / database
- D1-SCH-07 buffer rule (§6 predicate) / buf=15, booking ends 11:30 → 11:30 start rejected, 11:45 start passes / as asserted + ops / unit+database
- D1-SCH-08 backfill / pre-D rows → apply 0011 → owner resource everywhere, identical windows/counts / ledger + row audit / database (empty+legacy)
- D1-SCH-09 evaluator parity / tool vs owner API vs display → identical verdicts / n/a / integration
- D1-SCH-10 adjacent-slot buffer race / two adjacent free slot rows, same resource, buf=15 → concurrent bookings 11:00 + 11:30 → exactly one books (buffer conflict serialized on resource lock) / one booking / database concurrency
- D1-SCH-11 site capacity / property cap=1, booking exists → second overlapping booking (other resource) → rejected `site_capacity`; cap NULL → allowed / as asserted / database
- D1-SCH-12 deactivation guard / resource with future confirmed booking → deactivate → rejected with booking list; after cancel → succeeds / no partial deactivation / database
- D1-SCH-13 lock order / concurrent owner/agent/autonomy commits → all complete, no deadlock, serializable outcomes / all bookings valid / database concurrency
- D1-SCH-14 resource-swap replacements / two concurrent replacements swapping resources (R1→R2, R2→R1) → both complete via ascending-UUID order, no deadlock, no double-book / both bookings valid / database concurrency
- D1-SCH-15 multi-resource acquisition / one transaction locking R2+R1 (requested unordered) acquires R1→R2; concurrent inverse request serializes identically / lock-order audit via test hooks / database concurrency
- D1-APR-01 auto-pass / clean request → `proposed→approved→booked` + approval/`policy` decision + booking / four linked rows + activities/ops / database
- D1-APR-02 exception / Tue request → `awaiting_owner` + reasons + alternatives; owner confirms Wed → booked / full chain / database
- D1-APR-03 integrity denial / conflicting window + owner override attempt → rejected, stays `awaiting_owner` / no approval/booking rows + rejection audit / database
- D1-APR-04 expiry / past `expires_at` → `expired`, commit rejected / terminal, no booking / database
- D1-APR-05 changed availability / approved then taken → back to `awaiting_owner` + fresh alternatives / no booking / database concurrency
- D1-APR-06 window triple / normalized request → requested ≠ confirmed fields both persisted; owner alternative → approved fields + substitution notice queued data / triple + notice / database
- D1-APR-07 two clocks / proposal TTL lapse → `expired/proposal_lapsed`; owner window uses 72h/start expiry; each enforced independently / exact states / database
- D1-APR-08 notice decay / request passes 16h at request time; commit instant leaves 15h → commit recomputes and rejects `short_notice` → `awaiting_owner` / no booking + recompute audit / database
- D1-APR-09 approval-window binding / owner approves outside-hours exception for 19:00; commit attempted for 20:00 → hash mismatch ⇒ stale ⇒ `awaiting_owner`, new decision required / no booking / database
- D1-APR-10 policy change mid-flight / hours narrowed after approval → commit evaluates current text; recorded-category failure passes, new-category failure ⇒ stale / exact per-gate outcomes / database
- D1-APR-11 resource lost after approval / resource deactivated post-approval → commit denies `resource_unavailable`, never books / no booking rows / database
- D1-APR-12 exception honored / approved outside-hours exception, all integrity current → commit books / booking + decision chain / database
- D1-APR-13 non-overridable bypass attempt / approval presented against a conflicting window → denied `resource_conflict` regardless of recorded exceptions / no rows + denial audit / database
- D1-APR-14 expired approval / commit past `expires_at` → terminal rejection, re-request required / no booking / database
- D1-APR-15 stop blocks autonomous commit / active stop + policy-clean proposed request → commit refused, request `awaiting_owner` + reason `emergency_stop`, zero approval/booking rows / truthful audit / database
- D1-APR-16 exception cannot bypass stop / valid owner exception approval + active stop → autonomous commit still refused (lift stop or use direct owner workflow) / no booking rows / database
- D1-APR-17 direct owner unaffected / active stop + direct owner prepare/commit → books normally with existing safeguards / booking + unchanged owner audit / database
- D1-APR-18 relift revalidates / stop lifted after APR-15 block → re-attempt runs full policy+integrity revalidation before booking / fresh evaluation audit / database
- D1-ID-01 observed isolation / unknown identity → isolated lead+conversation+`observed` link / rows + `channel` actor / database
- D1-ID-02 no attach on match / existing phone X, unlinked inbound from X → NEW isolated lead, existing zero-write / isolation audit / database
- D1-ID-03 owner link / observed + canonical → superseded + `active` + verified destination; isolated lead archived, its conversation byte-identical / link rows + archive + evidence / database
- D1-ID-04 concurrent links / two link targets race → one live row, loser conflicts / partial unique + both audits / database concurrency
- D1-ID-05 revoke quarantine / revoke → next inbound mints fresh `observed` + isolated lead / chain preserved / database
- D1-ID-06 canonical conversation / link when open canonical exists → reused id returned; else created; isolated conversation `lead_id`/history unchanged / exact rows / database
- D1-ID-07 first-inbound race / concurrent first sightings → single observed row + single isolated lead / unique holds / database concurrency
- D1-POL-01 stop / stop=true → staging/evaluator refuses `stopped`, zero provider surface / skip audit / unit+database
- D1-POL-02 opt-out / opted_out → refused; re-permission owner-evidence only / no send rows / database
- D1-POL-03 suppression / keyword inbound → suppression row + permission flip, zero replies; revoke/re-observe/relink preserves suppression / suppression persists / database
- D1-POL-04 cap accounting / at cap → refuse; failure refunds; unknown holds; resend consumes new / exact `used` values / database concurrency
- D1-POL-05 seq backfill / pre-D messages → ordered seqs, unique per conversation / backfill audit / database
- D1-LEG-01 pending v1 / pre-D pending approval → owner decides → books with owner resource recorded / booking + v1 decision intact / database
- D1-LEG-02 approved v1 / pre-D approved → commit → books, owner resource / booking + audit / database
- D1-LEG-03 consumed v1 / consumed approval → reuse rejected terminal; replay returns stored outcome / zero new rows / database
- D1-LEG-04 operation replay / completed pre-D operation → same key → byte-identical stored response / zero recompute / database
- D1-LEG-05 comparator matrix / v1/v2 payload pairs → version detected, equality per version, cross-version never equal / n/a / unit
- D1-LEG-06 historical v1 replay / pre-D stored operation → same key + same payload → byte-identical stored response / zero recompute, zero new rows / database
- D1-LEG-07 omitted optionals / legacy payload re-sent through new code (D1 keys absent) → v1 projection matches stored digest → replays / response bytes identical / database
- D1-LEG-08 explicit resource vs v1 key / existing v1 key + same legacy args + explicit different `resource_id` → v2 selected exclusively, mismatch ⇒ different-operation conflict, zero writes (no stripped-v1 replay) / zero new rows + rejection audit / database
- D1-LEG-09 changed payload old key / different payload under an existing key → different-operation rejection (both v1 and v2 branches) / zero mutation / database
- D1-LEG-10 response bytes / v1 replay after migration → response jsonb bit-identical to pre-migration read / byte comparison / database
- D1-LEG-11 new v2 operation / explicit resource selection → v2 fingerprint stored; replay by same key+payload succeeds / v2 row + replay audit / database
- D1-LEG-12 mixed handling / v1 replay, v2 create, v1-mismatch-reject in one session → each follows its branch deterministically / exact per-key outcomes / database
- D1-LEG-13 explicit channel/destination vs v1 key / selector unit test: D1 keys present + non-null → v2 exclusively; vs stored v1 digest ⇒ conflict, zero writes, no v1 fallback / rejection audit / unit
- D1-LEG-14 v2 key changed resource / existing v2 key + changed `resource_id` → v2 mismatch ⇒ conflict, zero writes / zero new rows / database
- D1-LEG-15 defaults never select v2 / defaults-included dump with unprovided `channel="email"`/null D1 keys → `model_fields_set` inspection selects v1 / selection proof / unit
- D1-LEG-16 legacy fallback intact / LeadUpdate without timeframe replays via guarded `legacy_digest` exactly as today; any payload change still rejected / byte-identical replay + rejection cases / database
- D1-SNP-01 booking v2 schema / v2 booking payload → validated (resource+buffer, no channel fields); cross-domain field ⇒ `invalid_arguments` / n/a / unit
- D1-SNP-02 followup v2 schema / v2 followup payload → validated (channel/binding/category/consent, no resource fields); cross-domain field ⇒ rejected / n/a / unit
- D1-SNP-03 independent readers / v1/v2 × booking/followup payloads → correct reader, version detected, normalization exact / n/a / unit
- D1-SNP-04 serializer round-trip / v2 payloads serialize → parse → equal; v1 payloads never gain v2 keys / byte-exact / unit
- D1-MIG-01 0011 checks / empty + legacy → CHECK/actors/third-value/EXCLUDE/legacy/rollback/RLS per split / ledger / database

### D2 matrix — channels, closed messaging, webhooks, booking invocation, dispatcher, summaries

- D2-ID-01 attested-but-unlinked / new wa_id → isolated lead+thread, generic catalog reply, link-lookup-only reads / isolated rows / database
- D2-ID-02 pre-link denial / unlinked asks for canonical data → isolated context only / no mutation / database
- D2-ID-03 linked continuity / `active` → canonical conversation + verified context / same lead rows / database
- D2-ID-04 revoked inbound / revoked identity → fresh isolation; old-link binding fails / quarantine / database
- D2-ID-05 token isolation post-link / website token pre-link → post-link reads isolated thread only; canonical → 403 / zero token privilege change / database
- D2-ID-06 revoke vs queued / revoke with queued rows → rows `skipped_link_invalid`, owner-visible; no silent rebind / exact states / database
- D2-WWW-01 isolated starts preserved / website start with asserted email/phone → isolated lead+conversation, never canonical / isolated rows only / database
- D2-WWW-02 token scope frozen / pre-link token used post-link → identical access (isolated thread), canonical 403 / zero scope change / database
- D2-WWW-03 conversation-id knowledge / token for conversation A presented against B → rejected as today / no cross access / database
- D2-WWW-04 asserted email / start asserting a canonical lead's email → isolated lead (C2 alias rule), no canonical read / isolation audit / database
- D2-WWW-05 history immobility / link/unlink/revoke cycles → visitor thread rows byte-identical, lead_id unchanged / byte comparison / database
- D2-WWW-06 future-linking scope / simulated future canonical handoff → original token scope provably unchanged / scope assertion / database
- D2-MSG-01 unknown phrase id / output cites missing catalog id → schema/catalog rejection → fallback; nothing unvalidated sent / rejection + fallback audit / integration (adversarial)
- D2-MSG-02 unbound slot / slot references non-registry/stale/unshareable fact → rejected → fallback / re-read evidence / integration (adversarial)
- D2-MSG-03 paraphrased promise / “we can lower the price” → no catalog id expresses it ⇒ sensitive escalation, no send / draft+approval+`pending_owner` / integration (adversarial)
- D2-MSG-04 sold-as-available / inactive property referenced → fact re-read fails → fallback / nothing sent / integration (adversarial)
- D2-MSG-05 injection / override instructions in customer/property text → gates unchanged, input quarantined+logged / zero action rows / integration (adversarial)
- D2-MSG-06 free-text construction proof / output containing any `text` key → schema rejection; payload provably catalog⊕facts only / rejection audit / integration (adversarial)
- D2-MSG-07 mutated value / altered time/price vs server values → mismatch ⇒ rejected; server render authoritative / server-only values / integration (adversarial)
- D2-MSG-08 fallback exactness / repeated failure → exactly one fixed clarification + escalation, no partials / fallback + draft / integration
- D2-MSG-09 FAQ preserved / faq_answer → B3 quote-only assembly + optional catalog ACK / grounded + citations / integration
- D2-WEB-01 signature matrix / bad/missing sig, wrong account, oversize, malformed → 401/400, zero rows / n/a / unit+integration
- D2-WEB-02 delayed+duplicate / 2-day-old + replayed id → processed once / one event row + outcome / database concurrency
- D2-WEB-03 receipt identity / sent+delivered+read one wamid → three distinct events, ordered advance / three rows / database
- D2-WEB-04 conflict / same id different hash → `conflict`, first stands, quarantine visible / both recorded / database
- D2-WEB-05 multi-entry + stuck / 3-entry batch, one crash → per-entry states; sweeper ≤3 → `dead`+visible / exact rows / database
- D2-WEB-06 crash replay / crash after persist pre-process → re-drive from stored payload yields identical outcome, no dupes / replay audit / database (crash-point)
- D2-WEB-07 conflict preserves original / same id different hash → original row/state untouched, conflict quarantined, original recovery completes / original outcome + quarantine row / database
- D2-WEB-08 repeated conflicts / 12 conflicting arrivals → 10 quarantine rows + counter=2, original unaffected / exact counts / database
- D2-BOK-01 adapter e2e / “tomorrow 11” → extract → confirm → D1 commit → server-rendered confirmation / full chain / database+browser
- D2-BOK-02 alternatives/insist / taken → alternatives; insist → `awaiting_owner` / no booking / database
- D2-SUM-01 no-skip race / appends race boundary establishment → every committed message covered exactly once / seq audit / database concurrency
- D2-SUM-02 idempotent regeneration / concurrent same-boundary generation → single summary row / unique holds / database concurrency
- D2-SUM-03 degraded fallback / summary failure → tail-only + `context_degraded` flag + owner badge; assistant clarifies instead of asserting / flag + badge state / integration
- D2-DLV-01 inline dispatch / validated reply → staged → submitting → `sent` → receipt `delivered` / outbox+attempt+receipt / integration
- D2-DLV-02 staged crash / worker lost pre-transition → same attempt id auto re-driven, single submission / one attempt row / integration (crash-point)
- D2-DLV-03 submitting crash / crash post-transition pre-ack → `unknown`; zero auto-resume; owner resolves / unknown + owner action / integration (crash-point)
- D2-DLV-04 owner resend / unknown → authorized resend → new attempt, disclosed risk, same outbox id / attempt chain + disclosure / integration
- D2-MIG-01 0012 checks / CHECK/`validated`/FK/RLS/rollback per split / ledger / database

### D3 matrix — scheduled execution, suppression, limits, recovery

- D3-SCH-01 reminder once / booking + policy → enqueue at commit → single send at `not_before` / outbox+attempt+receipt / database
- D3-SUP-01 handled / reply after enqueue → skipped `handled` / skip + reason / database
- D3-SUP-02 cancelled trigger / booking cancelled → skipped `stale_trigger` / skip / database
- D3-SUP-03 stop mid-run / stop mid-batch → rest skip; in-flight completes unrecalled with truthful states / exact per-row / database concurrency
- D3-LIM-01 concurrent staging / two workers one row → one staged attempt / fencing audit / database concurrency
- D3-LIM-02 cap boundary / cap reached mid-batch → exact cap sends, overflow skips / exact `used` / database concurrency
- D3-LIM-03 cooldown with open attempts / open attempt + recent send → refused until window clears / reservation state / database
- D3-LIM-04 stop vs transition boundary (fourth review: staging commit is NOT the boundary) / stop committed before the staged→submitting transition ⇒ blocked + refunded; after ⇒ no recall, sent/unknown truthful / exact states / database concurrency
- D3-LIM-05 gate-change vs transition boundary (fourth review: rechecked at transition commit) / opt-out/disable/suppression/link/policy commits ordered around the transition ⇒ block-before/proceed-after split with refund-or-hold accounting / exact states / database concurrency
- D3-LIM-06 opt-out before staging / opted-out lead → staging refused, nothing reserved / zero rows + skip audit / database
- D3-LIM-07 opt-out staged→transition / opt-out commits after staging, before transition → transition recheck blocks; attempt `blocked`, outbox `queued`, reservation refunded / exact states + `used` restored / database concurrency
- D3-LIM-08 stop staged→transition / stop commits in the window → blocked + refunded, zero provider calls / exact states / database concurrency
- D3-LIM-09 disable staged→transition / policy disabled in the window → blocked + refunded / exact states / database
- D3-LIM-10 stop post-transition / stop commits after transition, before response → no recall; attempt completes sent/unknown truthfully, later rows skip / exact per-row states / database concurrency
- D3-LIM-11 concurrent transition / two workers authorize one staged attempt → exactly one commits; loser aborts pre-call / single `submitting` + fencing audit / database concurrency
- D3-LIM-12 blocked accounting / blocked/abandoned/failed/refunded vs unknown-held vs resend-new across a batch → `used` reconciles exactly / cap ledger audit / database
- D3-DLV-01 lease/fencing / stale worker write → fencing rejects; expired staged re-drives; expired submitting → unknown / single outcomes / database concurrency (crash-point)
- D3-DLV-02 dead-letter / definitive ×3 → `dead` + history; requeue = new attempt same outbox / chain / database
- D3-DLV-03 receipts / duplicate + late + unlinked → idempotent advance; unlinked surfaced never applied / exact state / database concurrency
- D3-DLV-04 no bypass / open attempt exists → new-key retry rejected / partial unique / database

### Shared

B1–B5 + Phase C regressions green (full backend once after final shared-path edit; frontend tests/lint/typecheck/build once); extended fictional browser journey (chat + WhatsApp sandbox threads, auto-book, exception approval, reminder, stop drill, unknown resolution, suppression drill) with exact fixture cleanup; mock provider in tests only; no real customer contact. Security review retained (signature matrix, rebinding, downgrade/cap races, redaction, minimization, tunnel/hosting review, owner-witnessed sandbox reconciliation) before any real delivery; production needs verification + template approvals + hardening. No production-readiness claim without real provider, security and deployment verification.

## 19. Credentials, cost, environments

Meta App + `WHATSAPP_TOKEN`, phone-number ID, App Secret, verify string (each separately owner-authorized, server-only); owner-operated public HTTPS for real webhooks. Inbound/sandbox free; outbound Meta-billed per message/conversation by country/category — verify the rate card at authorization, no prices quoted. Local mock testing costs nothing new. Demo-grade only until security review + restricted DB role, HTTPS, monitoring, backups.

## 20. Exclusions

General autonomous agents beyond the bounded pipeline; new model tools that send/book/schedule/change policy; autonomous negotiation, pricing, discounts, financial/legal commitments; bulk messaging, campaigns, imported lists; unbounded scheduling; email/SMS/calendar delivery; lead merge/dedupe; resource pools; vector search; realtime sockets; background daemons/workers/cron (explicit bounded entry points only); unapproved integrations; multi-tenancy; production/compliance/data-retention claims.

## 21. Unresolved decisions (deferred externals/values only — D1 architecture closed)

1. WhatsApp business verification and template-approval timeline (external; sandbox unblocks D2 tests).
2. Deployment-provided D3 processing cadence (deployment concern; app entry points idempotent and bounded).
3. Numeric value confirmation (caps, cooldowns, budgets per §15 — values adjustable without structural re-review).
4. Multilingual opt-out keywords and pool semantics (post-D extensions).
5. Catalog phrase/question inventory approval (content review at D2; structure fixed here).

## 22. Dependencies

Implementation authorization (separate step); Meta App + test-number credentials for sandbox delivery; HTTPS endpoint for real webhooks; business verification + template approvals for production. None authorized by this contract.
