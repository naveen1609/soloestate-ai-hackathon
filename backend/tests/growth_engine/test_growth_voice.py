"""Contract and transport verification tests for Meta Muse Voice Transcribe (/v1/asr/transcribe).
Tests:
- Documented multipart transport (request JSON metadata + audio binary).
- Supported audio format (WAV only; rejects non-WAV formats).
- Magic bytes verification (RIFF WAVE header).
- Upload size limits before buffering.
- Documented response schema parsing (transcript/text, confidence, language).
- Provider error handling (HTTP 4xx/5xx, timeouts, connection errors).
- Removal of falsely labelled raw-text voice transcription.
- Opt-in live provider test.
"""

import base64
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import VoiceTranscribeRequest
from app.growth_engine.services.voice_service import (
    MAX_AUDIO_BYTES,
    MAX_BASE64_LENGTH,
    MetaMuseVoiceService,
    VoiceServiceError,
    validate_audio_magic_bytes,
)

# Standard 44-byte RIFF WAVE header with dummy PCM data
VALID_WAV_HEADER = b"RIFF,\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
VALID_WAV_B64 = base64.b64encode(VALID_WAV_HEADER).decode("utf-8")


def test_voice_contract_successful_transcribe_multipart():
    """Verify Meta Voice transport calls POST /v1/asr/transcribe with multipart request and audio parts."""
    captured_requests = []

    def handler(request: httpx.Request):
        captured_requests.append(request)
        assert "/asr/transcribe" in str(request.url)
        assert request.method == "POST"
        assert "multipart/form-data" in request.headers.get("content-type", "")
        assert "Bearer" in request.headers.get("authorization", "")

        # Inspect request body bytes for multipart boundaries and fields
        body = request.read()
        assert b'name="request"' in body
        assert b'"audioEncoding": "WAV"' in body
        assert b'"mode": "PUSH_TO_TALK"' in body
        assert b'name="audio"' in body
        assert b'filename="audio.wav"' in body

        return httpx.Response(
            200,
            json={
                "transcript": "Spacious two bedroom luxury apartment in Indiranagar with morning sunlight.",
                "confidence": 0.98,
                "language": "en",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(
        GrowthEngineSettings(model_api_key="test-key-12345"),
        http_client=client,
    )

    prop_id = uuid4()
    req = VoiceTranscribeRequest(
        property_id=prop_id,
        audio_base64=VALID_WAV_B64,
        audio_format="wav",
    )
    res = service.transcribe_audio(req)

    assert len(captured_requests) == 1
    assert "two bedroom luxury apartment" in res.transcript
    assert res.confidence == 0.98
    assert res.detected_language == "en"
    assert res.model_id == "muse-voice-transcribe-1.0"


def test_voice_contract_schema_text_fallback():
    """Verify parsing when provider response returns 'text' instead of 'transcript'."""
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"text": "Sunlit balcony with garden view.", "confidence": 0.92})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(
        GrowthEngineSettings(model_api_key="test-key"),
        http_client=client,
    )

    req = VoiceTranscribeRequest(property_id=uuid4(), audio_base64=VALID_WAV_B64, audio_format="wav")
    res = service.transcribe_audio(req)
    assert res.transcript == "Sunlit balcony with garden view."
    assert res.confidence == 0.92


def test_voice_contract_unsupported_audio_formats_rejected():
    """Verify non-WAV audio formats are rejected by the Meta Voice transport."""
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"))
    prop_id = uuid4()

    for fmt in ["webm", "ogg", "mp3", "m4a", "flac"]:
        req = VoiceTranscribeRequest(
            property_id=prop_id,
            audio_base64=VALID_WAV_B64,
            audio_format=fmt,
        )
        with pytest.raises(VoiceServiceError) as exc_info:
            service.transcribe_audio(req)
        assert exc_info.value.code == "unsupported_audio_format"
        assert "Supported format is 'wav'" in str(exc_info.value)


def test_voice_contract_magic_bytes_mismatch_rejected():
    """Verify audio binary that does not match RIFF WAVE headers is rejected."""
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"))
    fake_audio = base64.b64encode(b"NOT_A_WAV_FILE_HEADER_DATA_1234567890_PADDING_TO_EXCEED_44_BYTES_LONG").decode("utf-8")

    req = VoiceTranscribeRequest(
        property_id=uuid4(),
        audio_base64=fake_audio,
        audio_format="wav",
    )
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "invalid_audio_magic_bytes"


def test_voice_contract_upload_size_limit_enforced():
    """Verify upload limits are enforced before buffering or decoding oversized payloads."""
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"))

    # Case A: Base64 string exceeds MAX_BASE64_LENGTH (~14MB)
    huge_b64 = "A" * (MAX_BASE64_LENGTH + 100)
    req_huge_b64 = VoiceTranscribeRequest(
        property_id=uuid4(),
        audio_base64=huge_b64,
        audio_format="wav",
    )
    with pytest.raises(VoiceServiceError) as exc_b64:
        service.transcribe_audio(req_huge_b64)
    assert exc_b64.value.code == "audio_payload_too_large"

    # Case B: Decoded bytes exceed MAX_AUDIO_BYTES (10MB)
    raw_overflow = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * (MAX_AUDIO_BYTES + 100)
    b64_overflow = base64.b64encode(raw_overflow).decode("utf-8")
    req_overflow = VoiceTranscribeRequest(
        property_id=uuid4(),
        audio_base64=b64_overflow,
        audio_format="wav",
    )
    with pytest.raises(VoiceServiceError) as exc_raw:
        service.transcribe_audio(req_overflow)
    assert exc_raw.value.code == "audio_payload_too_large"


def test_voice_contract_provider_error_400():
    """Verify provider 400 Bad Request error is explicitly parsed and raised."""
    def handler(request: httpx.Request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_audio_encoding",
                    "message": "Expected 16-bit linear PCM WAV format at 16kHz or 24kHz.",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = VoiceTranscribeRequest(property_id=uuid4(), audio_base64=VALID_WAV_B64, audio_format="wav")
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "invalid_audio_encoding"
    assert "Expected 16-bit linear PCM WAV" in str(exc_info.value)


def test_voice_contract_provider_error_500():
    """Verify provider 500 Internal Error is handled explicitly."""
    def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error in ASR model cluster")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = VoiceTranscribeRequest(property_id=uuid4(), audio_base64=VALID_WAV_B64, audio_format="wav")
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "provider_http_500"
    assert "Internal Server Error" in str(exc_info.value)


def test_voice_contract_timeout_handling():
    """Verify httpx.TimeoutException is converted to typed provider_timeout error."""
    def handler(request: httpx.Request):
        raise httpx.TimeoutException("ASR request timed out after 30 seconds")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = VoiceTranscribeRequest(property_id=uuid4(), audio_base64=VALID_WAV_B64, audio_format="wav")
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "provider_timeout"
    assert "timed out" in str(exc_info.value)


def test_voice_contract_unreachable_handling():
    """Verify httpx.ConnectError is converted to typed provider_unreachable error."""
    def handler(request: httpx.Request):
        raise httpx.ConnectError("Failed to establish connection to api.meta.ai")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = VoiceTranscribeRequest(property_id=uuid4(), audio_base64=VALID_WAV_B64, audio_format="wav")
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "provider_unreachable"


def test_voice_raw_text_rejected_from_voice_transcription():
    """Verify raw_text without audio is rejected and not falsely labelled as voice transcription."""
    service = MetaMuseVoiceService(GrowthEngineSettings(model_api_key="test-key"))
    req = VoiceTranscribeRequest(
        property_id=uuid4(),
        raw_text="Spacious 2 BHK home with modern fittings",
    )
    with pytest.raises(VoiceServiceError) as exc_info:
        service.transcribe_audio(req)
    assert exc_info.value.code == "missing_audio"
    assert "Raw text cannot be transcribed as voice" in str(exc_info.value)


def test_voice_brief_parsing_from_verified_transcript():
    """Verify structured marketing brief extraction from a verified transcript."""
    service = MetaMuseVoiceService(GrowthEngineSettings())
    prop_id = uuid4()
    transcript = "Luxury 3 BHK penthouse in Indiranagar with expansive private terrace and modular Italian kitchen."

    brief = service.parse_structured_brief(prop_id, transcript, input_mode="voice")
    assert brief.property_id == prop_id
    assert brief.input_mode == "voice"
    assert brief.tone == "luxury"
    assert len(brief.key_selling_points) > 0
    assert any("terrace" in p.lower() or "kitchen" in p.lower() for p in brief.key_selling_points)


@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_META_TESTS"),
    reason="Live Meta Voice provider test is opt-in via RUN_LIVE_META_TESTS",
)
def test_voice_live_provider_opt_in():
    """Live provider verification: executes only when explicitly opted-in with real credentials."""
    settings = GrowthEngineSettings()
    assert settings.model_api_key is not None, "RUN_LIVE_META_TESTS requires MODEL_API_KEY"

    service = MetaMuseVoiceService(settings)
    req = VoiceTranscribeRequest(
        property_id=uuid4(),
        audio_base64=VALID_WAV_B64,
        audio_format="wav",
    )
    res = service.transcribe_audio(req)
    assert res.transcript
    assert res.model_id == settings.muse_voice_model
