"""Real Meta inference only; no business actions or synthetic fallback responses."""

import asyncio
from typing import Literal

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import BaseModel, Field

from app.config import Settings

META_BASE_URL = "https://api.meta.ai/v1"


class ModelServiceError(Exception):
    """Safe to display: messages never include provider bodies or credentials."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ModelResult(BaseModel):
    response_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    text: str = Field(min_length=1)


class FunctionCall(BaseModel):
    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    arguments: str = Field(max_length=20000)


class AgentTurn(BaseModel):
    response_id: str
    model: str
    text: str
    calls: list[FunctionCall]
    replay: list[dict]


class ResponseContent(BaseModel):
    type: str
    text: str | None = None


class ResponseItem(BaseModel):
    type: str
    role: str | None = None
    status: str | None = None
    content: list[ResponseContent] = Field(default_factory=list)


class MetaResponse(BaseModel):
    """Validate our contract without requiring OpenAI-only usage metadata."""

    id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    status: Literal["completed"]
    error: None = None
    output: list[ResponseItem]


class MetaModelService:
    def __init__(self, settings: Settings, *, http_client: httpx.AsyncClient | None = None):
        key = settings.model_api_key
        if key is None or not key.get_secret_value().strip():
            raise ModelServiceError("configuration", "Set MODEL_API_KEY in backend/.env or the server environment.")
        if not settings.model_name.strip():
            raise ModelServiceError("configuration", "Set MODEL_NAME to your permitted Meta model ID.")
        self._model = settings.model_name.strip()
        self._timeout = settings.model_timeout_seconds
        self._client = AsyncOpenAI(
            api_key=key.get_secret_value().strip(),
            base_url=META_BASE_URL,
            timeout=self._timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.close()

    async def generate(self, prompt: str) -> ModelResult:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ModelServiceError("invalid_input", "Prompt must contain 1–20000 characters of nonblank text.")
        body = await self._request(input=prompt, max_output_tokens=2048, store=False)
        response = self._validate(body)
        text = self._text(response)
        if not text.strip():
            raise ModelServiceError("invalid_response", "Meta returned no usable text.")
        return ModelResult(response_id=response.id, model=response.model, text=text.strip())

    @staticmethod
    def _validate(body):
        try:
            return MetaResponse.model_validate(body, strict=True)
        except ValueError:
            raise ModelServiceError("invalid_response", "Meta returned an invalid response.") from None

    @staticmethod
    def _text(response):
        return "".join(content.text or "" for item in response.output
                       if item.type == "message" and item.role == "assistant" and item.status == "completed"
                       for content in item.content if content.type == "output_text")

    async def respond(self, history: list[dict], *, instructions: str, tools: list[dict]) -> AgentTurn:
        body = await self._request(input=history, instructions=instructions, tools=tools,
                                   parallel_tool_calls=False, max_output_tokens=4096, store=False,
                                   include=["reasoning.encrypted_content"])
        response = self._validate(body)
        calls, replay = [], []
        try:
            for item in body["output"]:
                if item["type"] == "function_call":
                    if item.get("status") not in (None, "completed"):
                        raise ValueError("Incomplete tool call")
                    calls.append(FunctionCall.model_validate(item, strict=True))
                    replay.append({key: item[key] for key in ("type", "id", "call_id", "name", "arguments", "status") if key in item})
                elif item["type"] == "reasoning":
                    if item.get("encrypted_content"):
                        if not isinstance(item["encrypted_content"], str) or not isinstance(item.get("summary", []), list):
                            raise ValueError("Invalid encrypted reasoning")
                        # Replay the opaque provider payload; never log/persist reasoning.
                        replay.append({key: item[key] for key in ("type", "id", "encrypted_content") if key in item} | {"summary": item.get("summary", [])})
                elif item["type"] == "message":
                    if item.get("role") != "assistant" or item.get("status") != "completed":
                        raise ValueError("Invalid assistant message")
                    replay.append(item)
                else:
                    raise ValueError("Unsupported output type")
            if len(calls) > 4 or len({call.call_id for call in calls}) != len(calls):
                raise ValueError("Invalid function call batch")
            if not calls and not self._text(response).strip():
                raise ValueError("No usable output")
            return AgentTurn(response_id=response.id, model=response.model, text=self._text(response), calls=calls, replay=replay)
        except (ValueError, KeyError, TypeError):
            raise ModelServiceError("invalid_response", "Meta returned invalid agent output.") from None

    async def _request(self, **kwargs) -> dict:
        try:
            # HTTP timeouts apply per operation; this also bounds total elapsed time.
            async with asyncio.timeout(self._timeout):
                raw = await self._client.responses.with_raw_response.create(model=self._model, **kwargs)
                return raw.http_response.json()
        except (TimeoutError, APITimeoutError):
            raise ModelServiceError("timeout", "Meta request timed out; no automatic retry was made.") from None
        except APIStatusError as exc:
            code = exc.status_code
            if code in (401, 403):
                error = ("authentication", "Meta rejected the credentials or model access; check MODEL_API_KEY and permissions.")
            elif code == 429:
                error = ("rate_limit", "Meta rate limit reached; retry later.")
            elif code == 404:
                error = ("model_unavailable", "Meta endpoint or model was not found; check MODEL_NAME and access.")
            else:
                error = ("provider_error", f"Meta request failed with HTTP {code}.")
            raise ModelServiceError(*error) from None
        except APIConnectionError:
            raise ModelServiceError("connection", "Could not connect to Meta; check network access.") from None
        except (APIError, ValueError, AttributeError, TypeError):
            raise ModelServiceError("invalid_response", "Meta returned an invalid response.") from None
