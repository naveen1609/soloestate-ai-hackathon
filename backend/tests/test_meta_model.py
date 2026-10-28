import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.meta_model import MetaModelService, ModelServiceError


def settings(**overrides):
    return Settings(_env_file=None, model_api_key="test-secret", model_name="test-model", **overrides)


def payload(**overrides):
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "test-model", "status": "completed", "error": None,
        "incomplete_details": None, "instructions": None, "metadata": {},
        "parallel_tool_calls": True, "temperature": 1, "tool_choice": "auto",
        "tools": [], "top_p": 1,
        "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                    "status": "completed", "content": [{"type": "output_text",
                    "text": "Hello SoloEstate", "annotations": [], "logprobs": []}]}],
        **overrides,
    }


def run(handler, prompt="Hello", config=None):
    async def call():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with MetaModelService(config or settings(), http_client=client) as service:
            return await service.generate(prompt)
    return asyncio.run(call())


def test_real_sdk_request_and_result():
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/responses"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {"model": "test-model", "input": "Hello", "max_output_tokens": 2048, "store": False}
        return httpx.Response(200, json=payload())
    result = run(handler)
    assert result.text == "Hello SoloEstate"
    assert result.response_id == "resp_test"
    assert result.model == "test-model"
    assert len(seen) == 1


def test_meta_usage_without_openai_cache_write_tokens():
    body = payload(usage={
        "input_tokens": 15,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 10},
        "total_tokens": 35,
    })
    assert run(lambda request: httpx.Response(200, json=body)).text == "Hello SoloEstate"


@pytest.mark.parametrize("changes", [
    {"id": 123}, {"model": None}, {"status": "incomplete"},
    {"error": {"message": "provider error"}}, {"output": "invalid"},
    {"output": [{"type": "message", "role": "user", "status": "completed", "content": [{"type": "output_text", "text": "Not an assistant reply"}]}]},
    {"output": [{"type": "message", "role": "assistant", "status": "incomplete", "content": [{"type": "output_text", "text": "Partial"}]}]},
    {"output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": 123}]}]},
])
def test_required_business_response_contract(changes):
    with pytest.raises(ModelServiceError) as exc:
        run(lambda request: httpx.Response(200, json=payload(**changes)))
    assert exc.value.code == "invalid_response"


@pytest.mark.parametrize("key,model", [(None, "model"), (" ", "model"), ("secret", " ")])
def test_missing_configuration(key, model):
    with pytest.raises(ModelServiceError, match="Set MODEL_") as exc:
        MetaModelService(Settings(_env_file=None, model_api_key=key, model_name=model))
    assert exc.value.code == "configuration"


@pytest.mark.parametrize("value", [0, -1, 121, float("inf"), float("nan")])
def test_timeout_configuration(value):
    with pytest.raises(ValidationError):
        settings(model_timeout_seconds=value)


@pytest.mark.parametrize("status,code", [(401, "authentication"), (403, "authentication"), (404, "model_unavailable"), (429, "rate_limit"), (500, "provider_error")])
def test_http_errors_are_safe_and_not_retried(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "test-secret provider details"}})
    with pytest.raises(ModelServiceError) as exc:
        run(handler)
    assert exc.value.code == code
    assert "test-secret" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection")])
def test_transport_errors(error, code):
    def handler(request):
        raise error("test-secret", request=request)
    with pytest.raises(ModelServiceError) as exc:
        run(handler)
    assert exc.value.code == code
    assert "test-secret" not in str(exc.value)


def test_total_deadline():
    async def handler(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=payload())
    with pytest.raises(ModelServiceError) as exc:
        run(handler, config=settings(model_timeout_seconds=0.01))
    assert exc.value.code == "timeout"


@pytest.mark.parametrize("body", [payload(status="incomplete"), payload(output=[]), payload(output=[{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": " ", "annotations": [], "logprobs": []}]}]), {}, payload(id="")])
def test_unusable_responses(body):
    with pytest.raises(ModelServiceError) as exc:
        run(lambda request: httpx.Response(200, json=body))
    assert exc.value.code == "invalid_response"


def test_non_json_response():
    with pytest.raises(ModelServiceError) as exc:
        run(lambda request: httpx.Response(200, text="not json"))
    assert exc.value.code == "invalid_response"


@pytest.mark.parametrize("prompt", ["", " ", "x" * 20001])
def test_invalid_prompt_never_calls_provider(prompt):
    def handler(request):
        pytest.fail("Invalid prompt must not reach the provider")
    with pytest.raises(ModelServiceError) as exc:
        run(handler, prompt)
    assert exc.value.code == "invalid_input"


def test_secret_is_redacted():
    assert "test-secret" not in repr(settings())


def test_settings_read_server_environment(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "environment-secret")
    monkeypatch.setenv("MODEL_NAME", "environment-model")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "12")
    config = Settings(_env_file=None)
    assert config.model_api_key.get_secret_value() == "environment-secret"
    assert config.model_name == "environment-model"
    assert config.model_timeout_seconds == 12


def test_connectivity_script_fails_without_key(monkeypatch, capsys):
    from scripts import check_meta

    monkeypatch.setattr(check_meta, "Settings", lambda: Settings(_env_file=None, model_api_key=None))
    assert asyncio.run(check_meta.check()) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert result["code"] == "configuration"
