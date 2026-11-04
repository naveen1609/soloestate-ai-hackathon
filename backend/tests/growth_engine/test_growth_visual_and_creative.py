"""Contract, geometry validation, and transport verification tests for Meta SAM 3.1.
Tests:
- Documented POST /v1/responses transport with sam-3.1 model and input_text / input_image.
- Strict coordinate, bounds, dimensions, and ordering validation for bounding boxes.
- Malformed and out-of-bounds cases (negative coordinates, > 1.0, inverted ordering, degenerate boxes).
- Mask parsing (RLE size/counts and SVG).
- Provider error handling (HTTP 4xx/5xx, timeouts, connection errors).
- Zero fabrication of fallback geometry in normal execution.
- Explicit demo fixture isolation.
- Opt-in live provider test.
- Creative concept generation using Meta Muse Image.
"""

import base64
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from uuid import uuid4

import httpx
import pytest

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    CreativeAssetResponse,
    CreativeGenerateRequest,
    PropertyGrounding,
    SAMSegmentRequest,
    SegmentedFeature,
)
from app.growth_engine.services.creative_service import MetaMuseImageService
from app.growth_engine.services.visual_intelligence_service import (
    MetaSAMService,
    VisualIntelligenceError,
    validate_bounding_box,
    validate_mask,
)


def sample_grounding() -> PropertyGrounding:
    return PropertyGrounding(
        property_id=uuid4(),
        title="Demo Cedar Apartment",
        locality="Indiranagar",
        price_inr=Decimal("10500000"),
        bedrooms=2,
        description="2 BHK apartment with modular kitchen and spacious balcony.",
        active=True,
        grounding_version=1,
        grounding_hash="a" * 64,
    )


# --- SAM 3.1 Contract & Fixture Tests ---

def test_sam_contract_successful_detection_and_transport():
    """Verify SAM 3.1 calls POST /v1/responses with documented contract and parses valid objects."""
    captured_requests = []

    contract_response = {
        "id": "resp_sam_test_123",
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({
                            "objects": [
                                {
                                    "id": "obj_balcony_1",
                                    "label": "Eastern Panoramic Balcony",
                                    "box": [0.10, 0.45, 0.90, 0.95],
                                    "confidence": 0.96,
                                    "mask": {
                                        "format": "rle",
                                        "size": [1080, 1920],
                                        "counts": "Zc0123456789_2",
                                    },
                                    "description": "Spacious covered balcony with clear morning view",
                                },
                                {
                                    "id": "obj_kitchen_1",
                                    "label": "Modular Kitchen",
                                    "box": [0.20, 0.05, 0.85, 0.50],
                                    "confidence": 0.91,
                                    "description": "Fitted modular kitchen counter",
                                },
                            ],
                            "architectural_summary": "Meta SAM 3.1 identified 2 prominent architectural elements.",
                        }),
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        captured_requests.append(request)
        assert "/responses" in str(request.url)
        assert request.method == "POST"
        assert "application/json" in request.headers.get("content-type", "")
        assert "Bearer" in request.headers.get("authorization", "")

        payload = json.loads(request.read())
        assert payload.get("model") == "sam-3.1"
        assert len(payload.get("input", [])) == 1
        content_items = payload["input"][0]["content"]
        assert any(c.get("type") == "input_text" for c in content_items)
        assert any(c.get("type") == "input_image" and c.get("image_url") == "https://example.com/photo.jpg" for c in content_items)

        return httpx.Response(200, json=contract_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    prop_id = uuid4()
    img_id = uuid4()
    req = SAMSegmentRequest(property_id=prop_id, image_id=img_id)
    res = service.segment_property_image(req, "https://example.com/photo.jpg")

    assert len(captured_requests) == 1
    assert res.is_authentic is True
    assert res.model_id == "sam-3.1"
    assert len(res.features) == 2
    assert res.features[0].label == "Eastern Panoramic Balcony"
    assert res.features[0].bounding_box == [0.10, 0.45, 0.90, 0.95]
    assert "rle:1080x1920" in (res.features[0].segmentation_mask_svg or "")
    assert res.features[1].label == "Modular Kitchen"


# --- SAM 3.1 Geometry & Bounding Box Validation Tests ---

def test_validate_bounding_box_valid():
    """Verify valid normalized coordinates pass."""
    box = validate_bounding_box([0.1, 0.2, 0.8, 0.9])
    assert box == [0.1, 0.2, 0.8, 0.9]


def test_validate_bounding_box_out_of_bounds_negative():
    """Verify negative coordinates are rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([-0.05, 0.2, 0.8, 0.9])
    assert exc.value.code == "invalid_bounding_box"
    assert "out of bounds" in str(exc.value)


def test_validate_bounding_box_out_of_bounds_exceeds_one():
    """Verify coordinates > 1.0 are rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([0.1, 0.2, 1.05, 0.9])
    assert exc.value.code == "invalid_bounding_box"
    assert "out of bounds" in str(exc.value)


def test_validate_bounding_box_inverted_vertical_ordering():
    """Verify ymin >= ymax is rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([0.8, 0.2, 0.3, 0.9])
    assert exc.value.code == "invalid_bounding_box"
    assert "Invalid vertical ordering" in str(exc.value)


def test_validate_bounding_box_inverted_horizontal_ordering():
    """Verify xmin >= xmax is rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([0.1, 0.85, 0.7, 0.3])
    assert exc.value.code == "invalid_bounding_box"
    assert "Invalid horizontal ordering" in str(exc.value)


def test_validate_bounding_box_degenerate_zero_dimensions():
    """Verify zero or near-zero area boxes are rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([0.5, 0.2, 0.5001, 0.8])
    assert exc.value.code == "invalid_bounding_box"
    assert "Degenerate bounding box" in str(exc.value)


def test_validate_bounding_box_non_numeric():
    """Verify non-numeric coordinates are rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box(["ymin", 0.2, 0.8, 0.9])
    assert exc.value.code == "invalid_bounding_box"


def test_validate_bounding_box_missing_dimensions():
    """Verify lists with length != 4 are rejected."""
    with pytest.raises(VisualIntelligenceError) as exc:
        validate_bounding_box([0.1, 0.2, 0.8])
    assert exc.value.code == "invalid_bounding_box"


def test_validate_mask_rle_structure():
    """Verify RLE mask validation."""
    valid_rle = {"format": "rle", "size": [1080, 1920], "counts": "abc123"}
    assert validate_mask(valid_rle) == "rle:1080x1920"

    with pytest.raises(VisualIntelligenceError) as exc:
        validate_mask({"format": "rle", "size": [0, -10], "counts": "abc"})
    assert exc.value.code == "invalid_mask"


# --- Provider Error & Fallback Prevention Tests ---

def test_sam_contract_provider_error_400():
    """Verify provider 400 Bad Request error is explicitly parsed and raised."""
    def handler(request: httpx.Request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "invalid_image_resolution",
                    "message": "Supplied image exceeds max dimensions of 4096x4096.",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = SAMSegmentRequest(property_id=uuid4(), image_id=uuid4())
    with pytest.raises(VisualIntelligenceError) as exc_info:
        service.segment_property_image(req, "https://example.com/huge.jpg")
    assert exc_info.value.code == "invalid_image_resolution"
    assert "exceeds max dimensions" in str(exc_info.value)


def test_sam_contract_timeout_handling():
    """Verify provider timeout is converted to typed provider_timeout error."""
    def handler(request: httpx.Request):
        raise httpx.TimeoutException("SAM request timed out")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = SAMSegmentRequest(property_id=uuid4(), image_id=uuid4())
    with pytest.raises(VisualIntelligenceError) as exc_info:
        service.segment_property_image(req, "https://example.com/photo.jpg")
    assert exc_info.value.code == "provider_timeout"


def test_sam_contract_unreachable_handling():
    """Verify provider connect error is converted to typed provider_unreachable error."""
    def handler(request: httpx.Request):
        raise httpx.ConnectError("Could not connect to Meta endpoint")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = SAMSegmentRequest(property_id=uuid4(), image_id=uuid4())
    with pytest.raises(VisualIntelligenceError) as exc_info:
        service.segment_property_image(req, "https://example.com/photo.jpg")
    assert exc_info.value.code == "provider_unreachable"


def test_sam_empty_detections_never_fabricates_fallback():
    """Verify that when SAM returns 0 detections, the service returns empty features without fabricating fake boxes."""
    contract_response = {
        "id": "resp_sam_empty",
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"objects": []}),
                    }
                ],
            }
        ],
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=contract_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)

    req = SAMSegmentRequest(property_id=uuid4(), image_id=uuid4(), demo_mode=False)
    res = service.segment_property_image(req, "https://example.com/photo.jpg")

    assert res.is_authentic is True
    assert len(res.features) == 0
    assert "no architectural features detected" in res.architectural_summary.lower()


def test_sam_demo_fixture_isolated_and_labelled():
    """Verify explicit demo mode returns clearly labelled demo fixtures and is_authentic=False."""
    service = MetaSAMService(GrowthEngineSettings(model_api_key="test-key"))
    img_id = uuid4()
    prop_id = uuid4()

    req = SAMSegmentRequest(property_id=prop_id, image_id=img_id, demo_mode=True)
    res = service.segment_property_image(req, "https://example.com/photo.jpg")

    assert res.is_authentic is False
    assert res.model_id == "demo_fixture"
    assert "demo fixture" in res.architectural_summary.lower()
    assert len(res.features) == 4
    assert all("demo_" in f.feature_id for f in res.features)


@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_META_TESTS"),
    reason="Live Meta SAM 3.1 provider test is opt-in via RUN_LIVE_META_TESTS",
)
def test_sam_live_provider_opt_in():
    """Live provider verification: executes only when explicitly opted-in with real credentials."""
    settings = GrowthEngineSettings()
    assert settings.model_api_key is not None, "RUN_LIVE_META_TESTS requires MODEL_API_KEY"

    service = MetaSAMService(settings)
    req = SAMSegmentRequest(
        property_id=uuid4(),
        image_id=uuid4(),
        demo_mode=False,
    )
    res = service.segment_property_image(req, "https://images.unsplash.com/photo-1545324418-cc1a3fa10c00?w=800")
    assert res.is_authentic is True
    assert res.model_id == settings.sam_model


# --- Creative Studio (Meta Muse Image) Tests ---

def test_muse_image_creative_generation():
    service = MetaMuseImageService(GrowthEngineSettings())
    grounding = sample_grounding()
    kit_id = uuid4()

    features = [
        SegmentedFeature(feature_id="f1", label="Eastern Balcony", confidence=0.95),
        SegmentedFeature(feature_id="f2", label="Modular Kitchen", confidence=0.92),
    ]

    req = CreativeGenerateRequest(
        kit_id=kit_id,
        property_id=grounding.property_id,
        asset_type="concept_graphic",
        creative_prompt="Create modern Instagram post showcasing bright morning lighting",
        style="modern",
    )

    asset = service.generate_creative_asset(req, grounding, features)
    assert asset.kit_id == kit_id
    assert asset.property_id == grounding.property_id
    assert asset.is_authentic is False
    assert "AI Creative Concept" in asset.concept_watermark
    assert len(asset.prompt_hash) == 64
    assert len(asset.image_url) > 0
    assert asset.status == "draft"


def test_muse_image_creative_mock_transport():
    seen = []
    from io import BytesIO
    from PIL import Image
    raw = BytesIO()
    Image.new("RGB", (10, 10), "red").save(raw, format="PNG")
    png_bytes = raw.getvalue()

    def handler(request: httpx.Request):
        seen.append(request)
        if "/images/generations" in str(request.url):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii")}]})
        elif "gen_123.png" in str(request.url):
            return httpx.Response(200, content=png_bytes, headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = MetaMuseImageService(GrowthEngineSettings(model_api_key="test-key"), http_client=client)
    grounding = sample_grounding()

    req = CreativeGenerateRequest(
        kit_id=uuid4(),
        property_id=grounding.property_id,
        creative_prompt="Exclusive luxury penthouse concept",
    )
    asset = service.generate_creative_asset(req, grounding)
    # Image bytes must be persisted as data URI to survive remote URL expiration
    assert asset.source == "muse_image"
    assert asset.image_url.startswith("data:image/png;base64,")
    with Image.open(BytesIO(base64.b64decode(asset.image_url.split(",", 1)[1]))) as exported:
        assert exported.size == (480, 62)
        assert exported.getpixel((240, 4)) == (255, 0, 0)
        assert (255, 255, 255) in exported.crop((0, 10, 480, 62)).get_flattened_data()
    assert len(seen) == 1


def test_sam_token_grammar_cookbook_specs():
    """Verify SAM 3.1 parser handles documented token grammar and cookbook specification formats."""
    from app.growth_engine.services.visual_intelligence_service import parse_sam_token_grammar

    wire = "<0f>0<|box;x1=1;y1=1;x2=2;y2=2;w=10;h=10;c=0.9|><|mask;x=0;y=0;c=0.9;data=2,2,!!!!!&T:u9`|>"
    result = parse_sam_token_grammar(wire)[0]
    assert result["box"] == [0.1, 0.1, 0.3, 0.3]
    assert "<path" in result["segmentation_mask_svg"]

    import json

    def respond(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["input"][0]["content"][0]["text"] == "balcony"
        events = [
            {"type": "response.content_part.done", "output_index": 0, "content_index": 0,
             "part": {"type": "output_text", "text": wire}},
            {"type": "response.completed"},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join(f"data: {json.dumps(e)}\n\n" for e in events))

    settings = GrowthEngineSettings(_env_file=None, model_api_key="fixture")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service = MetaSAMService(settings, http_client=client)
        req = SAMSegmentRequest(property_id=uuid4(), image_id=uuid4(), labels_to_detect=["balcony"])
        features = service.segment_property_image(req, image_url="https://example.test/photo.png").features
        assert len(features) == 1
        assert features[0].bounding_box == [0.1, 0.1, 0.3, 0.3]
        assert "<path" in features[0].segmentation_mask_svg

    # 1. XML-style token grammar
    xml_text = """
    <detection>
        <label>Eastern Panoramic Balcony</label>
        <box>[0.10, 0.45, 0.90, 0.95]</box>
        <confidence>0.96</confidence>
        <description>Balcony area with morning sun</description>
    </detection>
    <detection>
        <label>Granite Kitchen Counter</label>
        <box>[0.20, 0.05, 0.85, 0.50]</box>
        <confidence>0.92</confidence>
    </detection>
    """
    objects_xml = parse_sam_token_grammar(xml_text)
    assert len(objects_xml) == 2
    assert objects_xml[0]["label"] == "Eastern Panoramic Balcony"
    assert objects_xml[0]["box"] == [0.10, 0.45, 0.90, 0.95]
    assert objects_xml[0]["confidence"] == 0.96

    # 2. Line-based token grammar (cookbook prompt outputs)
    lines_text = """
    Balcony: [0.10, 0.45, 0.90, 0.95] (0.95)
    Living Room: [0.20, 0.05, 0.85, 0.50] (0.88)
    """
    objects_lines = parse_sam_token_grammar(lines_text)
    assert len(objects_lines) == 2
    assert objects_lines[0]["label"] == "Balcony"
    assert objects_lines[0]["box"] == [0.10, 0.45, 0.90, 0.95]
    assert objects_lines[0]["confidence"] == 0.95

    # 3. Fenced markdown JSON
    fenced_json = """```json
    {
        "objects": [
            {
                "id": "obj_1",
                "label": "Master Bedroom",
                "box": [0.15, 0.20, 0.80, 0.85],
                "confidence": 0.94
            }
        ]
    }
    ```"""
    objects_fenced = parse_sam_token_grammar(fenced_json)
    assert len(objects_fenced) == 1
    assert objects_fenced[0]["label"] == "Master Bedroom"


def test_creative_response_accepts_self_contained_image_bytes():
    """Check the model representation used to carry embedded image bytes."""
    from app.growth_engine.services.creative_service import validate_image_magic_bytes

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    mime = validate_image_magic_bytes(png_bytes)
    assert mime == "image/png"

    # Simulate an asset created with persisted image data
    b64_data = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
    kit_id = uuid4()
    prop_id = uuid4()
    asset_id = uuid4()

    asset = CreativeAssetResponse(
        id=asset_id,
        kit_id=kit_id,
        property_id=prop_id,
        asset_type="concept_graphic",
        is_authentic=False,
        concept_watermark="AI Creative Concept - Not Actual Property Photography",
        image_url=b64_data,
        prompt_text="Exclusive concept",
        prompt_hash="f" * 64,
        model_id="meta-models/muse-image-1.0",
        source="muse_image",
        status="draft",
        version=1,
        created_at=datetime.now(timezone.utc),
    )

    # Asset retains image data and concept watermark
    assert asset.image_url.startswith("data:image/png;base64,")
    assert "AI Creative Concept" in asset.concept_watermark
    # Simulate external URL expiry - asset is fully self-contained!
    assert "https://" not in asset.image_url
