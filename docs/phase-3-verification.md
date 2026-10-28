# Phase 3 verification — 27 October 2026

Implemented a real async Meta Model API service using OpenAI SDK 3.39.0, with backend-only configuration, sanitized errors, completed-response validation, a 2048-token output cap, a total deadline, and no automatic retries. The service calls the Responses API at Meta's fixed base URL. No mock fallback exists in production.

Reference: [official Meta quickstart](https://dev.meta.ai/docs/quickstart), checked during implementation for the base URL, MODEL_API_KEY, model ID example, and Responses API request format. No previous project code was used. Existing root `.env.example` and `test_meta_api.py` were preserved and are not used by this service.

Executed from `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.check_meta
Write-Output "Connectivity exit code: $LASTEXITCODE"
```

Results:

- Dependency installation succeeded.
- 30 tests passed, including the Phase 2 health test.
- One upstream Starlette/AnyIO deprecation warning remains.
- `pip check`: no broken requirements found.
- Connectivity script: safe `configuration` error, exit code 1, because backend MODEL_API_KEY is absent. Backend MODEL_NAME is also not configured. No live request was made.

Tests use HTTPX MockTransport through the installed OpenAI SDK. They verify URL, authorization, configured model, request options, response extraction, missing configuration, environment loading, timeout bounds, total deadline, transport failures, HTTP 401/403/404/429/500, absence of automatic retries, malformed/non-JSON/empty/incomplete output, invalid input, safe script failure, and secret redaction. They do not prove account access, provider availability, or live inference.

Frontend source and dependencies were unchanged in this phase; frontend checks were not repeated. Phase 2 evidence remains in `phase-2-verification.md`.

## Live verification and compatibility correction

After the owner configured credentials, a real request exposed an SDK response validation incompatibility: Meta omitted `usage.input_tokens_details.cache_write_tokens`, which the SDK's strict OpenAI schema required. Diagnostics printed only the exception type and validation field locations, never credentials or provider bodies.

The corrected service uses the SDK's raw response interface for transport and validates the provider JSON against an explicit Pydantic contract. Required response fields and completed assistant text remain strictly validated; unused usage metadata is ignored. A regression test covers Meta usage without cache_write_tokens, plus eight invalid contract cases.

Verification after the fix: **39 tests passed**, and `python -m scripts.check_meta` returned **exit code 0**, `status: ok`, model `muse-spark-1.3`, provider response ID `resp_6ae0a0374ffc5e39e5c641cf`, and a nonblank greeting. Live Meta inference is now confirmed. This performed no business action.
