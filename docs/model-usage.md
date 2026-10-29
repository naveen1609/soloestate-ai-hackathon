# Model usage

Meta Muse Spark is part of the running product. The backend sends the owner's goal to Meta Model API at https://api.meta.ai/v1 using the OpenAI-compatible Python SDK's Responses API. The configured and demonstrated model is muse-spark-1.3. MODEL_API_KEY and MODEL_NAME are loaded only by backend settings; use a model ID authorized for your account. The SDK is a transport compatibility layer, not an OpenAI inference provider in this application.

## What Meta does

Meta proposes a structured plan, selects registered business tools and their arguments, reads real function outputs, adapts after validation/tool failures, and proposes a structured finish referencing succeeded backend step sequences. It also generates personalized follow-up draft content when permitted. Business matching, authorization, transactions and consent are deterministic backend responsibilities.

The loop validates JSON objects, duplicate keys, schemas, permission mode, tool names and argument types. A read-only goal cannot gain mutation permissions from a model response. Draft-first tools can create drafts and request consent; they cannot approve consent or send customer messages. Actual tool results, including error codes, return to Meta. A finish must reference verified successful evidence and satisfy required plan tools; unsupported completion is rejected. Reports are built from persisted records rather than model prose.

The retained approval demo shows meaningful adaptation: two invalid approval calls failed validation; the model used actual feedback and obtained a valid pending review. No response or execution success is simulated in production. Automated tests may mock provider calls to verify repeatable failure behavior without spending credits.

## Connectivity and errors

From backend:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_meta
```

This makes one real billable request. Expected: status ok, response ID, returned model and nonblank text, exit zero. It verifies inference only, not business execution. Failures exit one with a controlled error code; there is no mock fallback. The end-to-end demo in [demo.md](demo.md) additionally verifies real tool use and persistence.

Missing configuration, authentication/access failure, rate limits, timeout, transport failure, incomplete output and invalid responses are surfaced as errors. SDK automatic retries are disabled. Agent limits and transaction retry rules are documented in [architecture.md](architecture.md). See [limitations](limitations.md) for privacy, retention and deployment constraints.

## Data and evidence

Goals and relevant fictional lead/property data, message draft content and tool outputs can be sent to Meta during execution. Owner-initiated conversation drafts also send retrieved FAQ excerpts and the simulated customer message; validation and persistence stay backend-owned. Do not use private real customer data in this demo. The client requests store=false; that request is not a guarantee about provider retention. Opaque encrypted reasoning is replayed in memory and is not a business audit narrative. PostgreSQL stores the submitted goal, validated plan, actual steps/results, state, owner decisions and provider response IDs/model metadata.

Live evidence is recorded in [Phase 6](phase-6-verification.md), [Phase 7](phase-7-verification.md), [Phase 8](phase-8-verification.md) and [Phase 10](phase-10-verification.md). These are dated observations, not promises that future API requests or model choices will be identical. Provider access, model availability and credits depend on the configured account.
