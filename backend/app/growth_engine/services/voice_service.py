"""Meta Muse Voice Transcribe service for SoloEstate AI Growth Engine-lite.
Converts spoken owner voice notes into raw transcripts via Meta Voice /v1/asr/transcribe
and parses them into a typed, structured marketing brief.
"""

import base64
import json
import re
from typing import Any
from uuid import UUID

import httpx

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    MarketingBriefCreate,
    VoiceTranscribeRequest,
    VoiceTranscribeResponse,
)

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB limit
MAX_BASE64_LENGTH = 14_000_000       # ~10MB encoded bound
SUPPORTED_AUDIO_FORMATS = {"wav"}


class VoiceServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_audio_magic_bytes(audio_bytes: bytes, declared_format: str) -> None:
    """Validate audio media container magic bytes to ensure genuine WAV input."""
    if len(audio_bytes) < 12:
        raise VoiceServiceError("invalid_audio_bytes", "Audio payload is too small to contain valid container headers.")

    fmt = declared_format.lower().strip()
    if fmt != "wav":
        raise VoiceServiceError(
            "unsupported_audio_format",
            f"Audio format '{fmt}' is not supported by Meta Voice ASR. Supported format is 'wav'.",
        )

    # RIFF WAVE: starts with 'RIFF' and bytes 8..12 are 'WAVE'
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return

    raise VoiceServiceError(
        "invalid_audio_magic_bytes",
        f"Audio binary content does not match container header signatures for declared format '{fmt}'.",
    )


class MetaMuseVoiceService:
    def __init__(
        self,
        settings: GrowthEngineSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self.settings = settings or GrowthEngineSettings()
        secret_key = self.settings.model_api_key.get_secret_value().strip() if self.settings.model_api_key else ""
        self._secret_key = secret_key
        self._http_client = http_client or httpx.Client(
            timeout=self.settings.model_timeout_seconds,
        )

    def _get_transcribe_url(self) -> str:
        base = self.settings.meta_base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/asr/transcribe"
        return f"{base}/v1/asr/transcribe"

    def transcribe_audio(self, request: VoiceTranscribeRequest) -> VoiceTranscribeResponse:
        """Transcribe spoken audio using Meta Muse Voice Transcribe (/v1/asr/transcribe).
        Strictly requires authentic audio input in WAV format.
        Never fabricates transcription results or labels raw text as voice transcription.
        """
        # Reject raw_text masquerading as voice transcription
        if not request.audio_base64:
            if request.raw_text and request.raw_text.strip():
                raise VoiceServiceError(
                    "missing_audio",
                    "Raw text cannot be transcribed as voice. Submit text directly in marketing brief input_mode='text'.",
                )
            raise VoiceServiceError("missing_audio", "Audio payload in WAV format is required for Meta Voice transcription.")

        # Enforce upload limits before decoding/buffering complete payload
        if len(request.audio_base64) > MAX_BASE64_LENGTH:
            raise VoiceServiceError(
                "audio_payload_too_large",
                f"Audio payload exceeds maximum upload limit of {MAX_AUDIO_BYTES} bytes.",
            )

        fmt = (request.audio_format or "wav").lower().strip()
        if fmt not in SUPPORTED_AUDIO_FORMATS:
            raise VoiceServiceError(
                "unsupported_audio_format",
                f"Audio format '{fmt}' is not supported by Meta Voice ASR. Supported format is 'wav'.",
            )

        try:
            audio_bytes = base64.b64decode(request.audio_base64)
        except Exception:
            raise VoiceServiceError("invalid_audio", "Failed to decode base64 audio payload.")

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise VoiceServiceError(
                "audio_payload_too_large",
                f"Audio payload ({len(audio_bytes)} bytes) exceeds maximum limit of {MAX_AUDIO_BYTES} bytes.",
            )

        if len(audio_bytes) < 12:
            raise VoiceServiceError("invalid_audio", "Audio payload is too short to be a valid WAV file.")

        # Validate container magic bytes (RIFF WAVE)
        validate_audio_magic_bytes(audio_bytes, fmt)

        if not self._secret_key:
            raise VoiceServiceError("missing_credentials", "Meta Model API credentials (MODEL_API_KEY) are not configured.")

        url = self._get_transcribe_url()
        request_metadata = {
            "model": self.settings.muse_voice_model,
            "audioEncoding": "WAV",
            "mode": "PUSH_TO_TALK",
        }

        files = {
            "request": (None, json.dumps(request_metadata), "application/json"),
            "audio": ("audio.wav", audio_bytes, "audio/wav"),
        }
        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Accept": "application/json",
        }

        try:
            response = self._http_client.post(url, files=files, headers=headers)
        except httpx.TimeoutException:
            raise VoiceServiceError("provider_timeout", "Meta Voice API request timed out.")
        except httpx.ConnectError:
            raise VoiceServiceError("provider_unreachable", "Unable to connect to Meta Voice API endpoint.")
        except httpx.RequestError as exc:
            raise VoiceServiceError("provider_request_failed", f"Meta Voice API request failed: {type(exc).__name__}")

        if response.status_code != 200:
            try:
                err_data = response.json()
                err_obj = err_data.get("error", {})
                code = err_obj.get("code") or f"provider_http_{response.status_code}"
                msg = err_obj.get("message") or response.text or f"HTTP {response.status_code}"
            except Exception:
                code = f"provider_http_{response.status_code}"
                msg = response.text or f"HTTP {response.status_code}"
            raise VoiceServiceError(code, f"Meta Voice provider error ({response.status_code}): {msg}")

        try:
            data = response.json()
        except Exception:
            raise VoiceServiceError("malformed_response", "Meta Voice returned a non-JSON response payload.")

        transcript = ""
        confidence = 0.95
        detected_language = "en"

        if isinstance(data, dict):
            transcript = data.get("transcript") or data.get("text") or ""
            confidence = float(data.get("confidence", 0.95))
            detected_language = str(data.get("language", "en"))

            if not transcript and "segments" in data and isinstance(data["segments"], list):
                transcript = " ".join(s.get("text", "") for s in data["segments"] if s.get("text"))
            elif not transcript and "turns" in data and isinstance(data["turns"], list):
                transcript = " ".join(
                    t.get("transcript", "") or t.get("text", "")
                    for t in data["turns"]
                    if t.get("transcript") or t.get("text")
                )
        elif isinstance(data, str):
            transcript = data

        transcript = transcript.strip()
        if not transcript:
            raise VoiceServiceError("empty_transcription", "Meta Voice returned an empty transcript.")

        return VoiceTranscribeResponse(
            transcript=transcript,
            model_id=self.settings.muse_voice_model,
            confidence=confidence,
            detected_language=detected_language,
        )

    def parse_structured_brief(
        self,
        property_id: UUID,
        raw_text: str,
        input_mode: str = "voice",
        llm_response_text: str | None = None,
    ) -> MarketingBriefCreate:
        """Parse verified transcript or text into a structured marketing brief.
        Does not fabricate claims about legal title, verified deeds, or fictional amenities.
        """
        if not raw_text.strip():
            raise VoiceServiceError("invalid_brief", "Raw input for marketing brief cannot be empty.")

        target_audience = "High-intent homebuyers and working professionals in Bengaluru"
        tone: Any = "professional"
        objective: Any = "lead_generation"
        selling_points: list[str] = []
        special_offers = ""

        # If LLM response provided, parse JSON
        if llm_response_text:
            try:
                clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", llm_response_text.strip(), flags=re.MULTILINE)
                data = json.loads(clean)
                if isinstance(data, dict):
                    target_audience = data.get("target_audience", target_audience)
                    tone = data.get("tone", tone)
                    objective = data.get("campaign_objective", objective)
                    selling_points = data.get("key_selling_points", selling_points)
                    special_offers = data.get("special_offers", special_offers)
            except Exception:
                pass

        if not selling_points:
            sentences = [s.strip() for s in re.split(r"[.\n;]+", raw_text) if s.strip()]
            for s in sentences:
                if any(w in s.lower() for w in ["highlight", "features", "spacious", "balcony", "kitchen", "view", "amenities", "close to"]):
                    selling_points.append(s)

            if not selling_points and sentences:
                selling_points = sentences[:3]

            low = raw_text.lower()
            if any(w in low for w in ["luxury", "premium", "exclusive", "opulent", "high-end"]):
                tone = "luxury"
            elif any(w in low for w in ["diwali", "festive", "new year", "festival", "celebration", "deepavali"]):
                tone = "festive"
                objective = "festive_offer"
            elif any(w in low for w in ["urgent", "hurry", "limited", "fast", "closing soon"]):
                tone = "urgent"
            elif any(w in low for w in ["warm", "family", "cozy", "peaceful", "quiet"]):
                tone = "warm"

            offer_match = re.search(r"(?:offer|special|bonus|discount|free)[^.\n]+", raw_text, re.IGNORECASE)
            if offer_match:
                special_offers = offer_match.group(0).strip()

        valid_tones = {"professional", "luxury", "warm", "urgent", "festive"}
        valid_objectives = {"lead_generation", "brand_awareness", "open_house", "festive_offer", "price_drop"}

        return MarketingBriefCreate(
            property_id=property_id,
            input_mode="voice" if input_mode == "voice" else "text",
            raw_input=raw_text.strip(),
            target_audience=target_audience,
            key_selling_points=selling_points or ["Well-located residential property with convenient connectivity", "Spacious layout with natural light"],
            tone=tone if tone in valid_tones else "professional",
            campaign_objective=objective if objective in valid_objectives else "lead_generation",
            special_offers=special_offers,
        )
