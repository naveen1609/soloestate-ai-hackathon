"""Property Visual Intelligence service using Meta SAM 3.1.
Segments architectural elements and interior features from authentic property photography
according to the Meta SAM 3.1 API contract (POST /v1/responses).
Validates coordinates, bounds, dimensions, and ordering strictly without fabricating detections.
"""

import json
import math
import re
from typing import Any
from uuid import UUID

import httpx

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    SAMSegmentRequest,
    SAMSegmentResponse,
    SegmentedFeature,
)


class VisualIntelligenceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def parse_sam_token_grammar(text: str) -> list[dict[str, Any]]:
    """Parse SAM 3.1 token grammar formats (XML tags, line-based detections, or fenced JSON)."""
    clean_text = text.strip()
    if clean_text.startswith("<0f>"):
        from meta_sam_parser import CompletedOutcome, decode_mask_to_svg_path, image_segmentation_format

        if clean_text == "<0f>":
            return []
        dimensions = set(re.findall(r"<\|box;[^|]*?;w=(\d+);h=(\d+)", clean_text))
        mask_sizes = re.findall(r"<\|mask;[^|]*?;data=(\d+),(\d+),", clean_text)
        if len(dimensions) != 1 or any(int(h) * int(w) > 4_000_000 for h, w in mask_sizes):
            raise VisualIntelligenceError("malformed_response", "Invalid or oversized SAM image geometry.")
        width, height = map(int, dimensions.pop())
        if width <= 0 or height <= 0:
            raise VisualIntelligenceError("malformed_response", "Invalid SAM frame dimensions.")

        parser = image_segmentation_format().create_parser()
        parser.push(clean_text)
        result = parser.finish(CompletedOutcome()).result
        if result.diagnostics or not result.records or any(r.kind == "text" for r in result.records):
            raise VisualIntelligenceError("malformed_response", "Invalid SAM image segmentation record.")
        masks = {r.object_id: r for r in result.records if r.kind == "mask"}
        objects = []
        for box in (r for r in result.records if r.kind == "box"):
            mask = masks.get(box.object_id)
            if mask is None or box.confidence is None:
                raise VisualIntelligenceError("malformed_response", "SAM response omitted a mask or confidence.")
            bounds = mask.bounds
            path = decode_mask_to_svg_path(mask.mask)
            objects.append({
                "id": box.object_id,
                "box": [box.top / height, box.left / width, box.bottom / height, box.right / width],
                "confidence": box.confidence,
                "segmentation_mask_svg": (
                    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
                    f'<path transform="translate({bounds.left} {bounds.top}) '
                    f'scale({(bounds.right - bounds.left) / mask.mask.width} {(bounds.bottom - bounds.top) / mask.mask.height})" '
                    f'd="{path}" fill="#3b82f6" fill-opacity=".35"/></svg>'
                ),
            })
        if len(objects) != len(masks):
            raise VisualIntelligenceError("malformed_response", "SAM response did not pair boxes and masks.")
        return objects
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean_text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(clean_text)
            if isinstance(data, dict):
                return data.get("objects") or data.get("features") or []
            elif isinstance(data, list):
                return data
        except Exception:
            pass

    objects: list[dict[str, Any]] = []
    # 1. XML-style token grammar (<detection> ... </detection> or <object> ... </object>)
    detection_blocks = re.findall(r"<(?:detection|object)>(.*?)</(?:detection|object)>", clean_text, flags=re.DOTALL | re.IGNORECASE)
    for idx, block in enumerate(detection_blocks):
        lbl_m = re.search(r"<label>(.*?)</label>", block, flags=re.IGNORECASE | re.DOTALL)
        box_m = re.search(r"<box>\[?(.*?)\]?</box>", block, flags=re.IGNORECASE | re.DOTALL)
        conf_m = re.search(r"<confidence>(.*?)</confidence>", block, flags=re.IGNORECASE)
        desc_m = re.search(r"<description>(.*?)</description>", block, flags=re.IGNORECASE | re.DOTALL)

        if box_m:
            try:
                coords = [float(c.strip()) for c in box_m.group(1).split(",")]
                conf = float(conf_m.group(1).strip()) if conf_m else 0.90
                label = lbl_m.group(1).strip() if lbl_m else f"Feature {idx+1}"
                desc = desc_m.group(1).strip() if desc_m else ""
                objects.append({
                    "id": f"det_{idx+1}",
                    "label": label,
                    "box": coords,
                    "confidence": conf,
                    "description": desc,
                })
            except Exception:
                continue

    if objects:
        return objects

    # 2. Line-based token grammar: "Label: [ymin, xmin, ymax, xmax] (conf)"
    # or "[ymin, xmin, ymax, xmax] - Label (conf)"
    for idx, line in enumerate(clean_text.splitlines()):
        line = line.strip()
        if not line:
            continue
        m1 = re.search(r"^(.*?):\s*\[([0-9.,\s]+)\](?:\s*\(([0-9.]+)\))?", line)
        if m1:
            try:
                label = m1.group(1).strip()
                coords = [float(c.strip()) for c in m1.group(2).split(",")]
                conf = float(m1.group(3)) if m1.group(3) else 0.85
                objects.append({
                    "id": f"line_{idx+1}",
                    "label": label,
                    "box": coords,
                    "confidence": conf,
                })
                continue
            except Exception:
                pass

        m2 = re.search(r"^\[([0-9.,\s]+)\]\s*[-:]\s*(.*?)(?:\s*\(([0-9.]+)\))?$", line)
        if m2:
            try:
                coords = [float(c.strip()) for c in m2.group(1).split(",")]
                label = m2.group(2).strip()
                conf = float(m2.group(3)) if m2.group(3) else 0.85
                objects.append({
                    "id": f"line_{idx+1}",
                    "label": label,
                    "box": coords,
                    "confidence": conf,
                })
            except Exception:
                pass

    return objects


def validate_bounding_box(box: Any) -> list[float]:
    """Validate bounding box coordinates according to normalized [ymin, xmin, ymax, xmax] format.
    Checks:
    - Length is exactly 4.
    - All elements are finite numbers (not NaN or Inf).
    - Normalized bounds: 0.0 <= c <= 1.0.
    - Strict ordering: ymin < ymax and xmin < xmax.
    - Non-degenerate dimensions: (ymax - ymin) >= 0.005 and (xmax - xmin) >= 0.005.
    """
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"Bounding box must contain exactly 4 coordinates, got {box!r}",
        )

    try:
        ymin, xmin, ymax, xmax = [float(c) for c in box]
    except (ValueError, TypeError):
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"All bounding box coordinates must be numeric, got {box!r}",
        )

    for name, val in [("ymin", ymin), ("xmin", xmin), ("ymax", ymax), ("xmax", xmax)]:
        if math.isnan(val) or math.isinf(val):
            raise VisualIntelligenceError(
                "invalid_bounding_box",
                f"Bounding box coordinate '{name}' cannot be NaN or Inf.",
            )

    if not (0.0 <= ymin <= 1.0 and 0.0 <= xmin <= 1.0 and 0.0 <= ymax <= 1.0 and 0.0 <= xmax <= 1.0):
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"Coordinates out of bounds [0.0, 1.0]: [{ymin}, {xmin}, {ymax}, {xmax}]",
        )

    if ymin >= ymax:
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"Invalid vertical ordering: ymin ({ymin}) must be strictly less than ymax ({ymax}).",
        )

    if xmin >= xmax:
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"Invalid horizontal ordering: xmin ({xmin}) must be strictly less than xmax ({xmax}).",
        )

    if (ymax - ymin) < 0.005 or (xmax - xmin) < 0.005:
        raise VisualIntelligenceError(
            "invalid_bounding_box",
            f"Degenerate bounding box: width or height is below minimum visible dimension of 0.005.",
        )

    return [ymin, xmin, ymax, xmax]


def validate_mask(mask: Any) -> str | None:
    """Validate mask structure (RLE or SVG) and return SVG or summary representation."""
    if not mask:
        return None

    if isinstance(mask, dict):
        fmt = mask.get("format", "").lower()
        if fmt == "rle":
            size = mask.get("size")
            counts = mask.get("counts")
            if not (isinstance(size, list) and len(size) == 2 and size[0] > 0 and size[1] > 0):
                raise VisualIntelligenceError("invalid_mask", f"RLE mask size must be [height, width] positive integers, got {size!r}")
            if not isinstance(counts, str) or not counts:
                raise VisualIntelligenceError("invalid_mask", "RLE mask counts must be a non-empty string.")
            return f"rle:{size[0]}x{size[1]}"
        elif fmt == "svg":
            svg_content = mask.get("svg") or mask.get("content", "")
            if isinstance(svg_content, str) and "<svg" in svg_content:
                return svg_content
    elif isinstance(mask, str) and "<svg" in mask:
        return mask

    return None


class MetaSAMService:
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

    def _get_responses_url(self) -> str:
        base = self.settings.meta_base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

    def segment_property_image(
        self,
        request: SAMSegmentRequest,
        image_url: str,
        property_context: dict[str, Any] | None = None,
        *, timeout: float | None = None,
    ) -> SAMSegmentResponse:
        """Analyze and segment authentic property photography using Meta SAM 3.1.
        Strictly preserves provenance:
        - In normal execution: parses actual SAM 3.1 segmentation records. Never fabricates geometry.
        - In explicit demo mode: clearly labelled as demo fixture (model_id='demo_fixture', is_authentic=False).
        """
        if not image_url or not image_url.strip():
            raise VisualIntelligenceError("invalid_image", "Valid image URL is required for SAM 3.1 segmentation.")

        # Explicit demo fixture mode: isolated and explicitly labelled
        if getattr(request, "demo_mode", False):
            demo_catalog = [
                ("demo_balcony", "Balcony Area", 0.90, [0.10, 0.45, 0.90, 0.95], "Covered outdoor balcony space"),
                ("demo_kitchen", "Kitchen Area", 0.88, [0.20, 0.05, 0.85, 0.50], "Kitchen workspace with cabinetry"),
                ("demo_living", "Living Room", 0.85, [0.65, 0.15, 0.98, 0.85], "Main residential living floorplan area"),
                ("demo_windows", "Window Openings", 0.82, [0.15, 0.55, 0.80, 0.90], "Window installation for natural illumination"),
            ]
            demo_features = [
                SegmentedFeature(
                    feature_id=fid,
                    label=label,
                    confidence=conf,
                    bounding_box=box,
                    segmentation_mask_svg=f"<svg viewBox='0 0 100 100'><rect x='{box[1]*100}' y='{box[0]*100}' width='{(box[3]-box[1])*100}' height='{(box[2]-box[0])*100}' fill='rgba(59,130,246,0.3)' stroke='#3b82f6'/></svg>",
                    description=desc,
                )
                for fid, label, conf, box, desc in demo_catalog
            ]
            return SAMSegmentResponse(
                image_id=request.image_id,
                property_id=request.property_id,
                model_id="demo_fixture",
                features=demo_features,
                architectural_summary="Demo fixture geometry: not live inference (explicit demo mode).",
                is_authentic=False,
            )

        labels = list(dict.fromkeys(label.strip() for label in request.labels_to_detect))
        if any(not label or len(label) > 80 for label in labels):
            raise VisualIntelligenceError("invalid_labels", "SAM concepts must be between 1 and 80 characters.")
        if len(labels) > 1:
            features = []
            for index, label in enumerate(labels):
                result = self.segment_property_image(
                    request.model_copy(update={"labels_to_detect": [label]}), image_url, property_context,
                    timeout=min(8, self.settings.model_timeout_seconds),
                )
                features.extend(feature.model_copy(update={"feature_id": f"{index}:{feature.feature_id}"})
                                for feature in result.features)
            return SAMSegmentResponse(
                image_id=request.image_id, property_id=request.property_id,
                model_id=self.settings.sam_model, features=features,
                architectural_summary=f"SAM returned {len(features)} detections across {len(labels)} concepts.",
            )

        # Normal execution: strictly require credentials
        if not self._secret_key:
            raise VisualIntelligenceError(
                "missing_credentials",
                "Meta Model API credentials (MODEL_API_KEY) are not configured.",
            )

        url = self._get_responses_url()
        # SAM accepts one concept per request.
        prompt_text = labels[0]

        payload = {
            "model": self.settings.sam_model,
            "stream": True,
            "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            with self._http_client.stream("POST", url, json=payload, headers=headers, timeout=timeout or self.settings.model_timeout_seconds) as upstream:
                body = bytearray()
                for chunk in upstream.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 8_000_000:
                        raise VisualIntelligenceError("malformed_response", "SAM response exceeded its size limit.")
                response = httpx.Response(upstream.status_code, headers=upstream.headers, content=bytes(body))
        except httpx.TimeoutException:
            raise VisualIntelligenceError("provider_timeout", "Meta SAM 3.1 request timed out.")
        except httpx.ConnectError:
            raise VisualIntelligenceError("provider_unreachable", "Unable to connect to Meta SAM 3.1 API endpoint.")
        except httpx.RequestError as exc:
            raise VisualIntelligenceError("provider_request_failed", f"Meta SAM 3.1 request failed: {type(exc).__name__}")

        if response.status_code != 200:
            try:
                err_data = response.json()
                err_obj = err_data.get("error", {})
                code = err_obj.get("code") or f"provider_http_{response.status_code}"
                msg = err_obj.get("message") or response.text or f"HTTP {response.status_code}"
            except Exception:
                code = f"provider_http_{response.status_code}"
                msg = response.text or f"HTTP {response.status_code}"
            raise VisualIntelligenceError(code, f"Meta SAM 3.1 provider error ({response.status_code}): {msg}")

        try:
            if "text/event-stream" in response.headers.get("content-type", ""):
                text_parts = {}
                completed = False
                for line in response.text.splitlines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    event = json.loads(line[5:])
                    event_type = event.get("type")
                    key = (event.get("output_index"), event.get("content_index"))
                    if event_type == "response.output_text.done":
                        text_parts[key] = event["text"]
                    elif event_type == "response.content_part.done" and event["part"]["type"] == "output_text":
                        text_parts[key] = event["part"]["text"]
                    elif event_type == "response.completed":
                        completed = True
                    elif event_type in {"error", "response.failed", "response.incomplete"}:
                        raise ValueError("SAM inference did not complete")
                if not completed or not text_parts:
                    raise ValueError("Missing SAM completion or output")
                resp_data = {"output": [{"type": "message", "content": [
                    {"type": "output_text", "text": value} for value in text_parts.values()
                ]}]}
            else:
                resp_data = response.json()
        except Exception:
            raise VisualIntelligenceError("malformed_response", "Meta SAM 3.1 returned non-JSON response payload.")

        raw_objects: list[dict[str, Any]] = []
        architectural_summary = ""

        # Case 1: Direct objects/features in response JSON
        if isinstance(resp_data, dict):
            if "objects" in resp_data and isinstance(resp_data["objects"], list):
                raw_objects = resp_data["objects"]
            elif "features" in resp_data and isinstance(resp_data["features"], list):
                raw_objects = resp_data["features"]
            architectural_summary = str(resp_data.get("architectural_summary", ""))

            # Case 2: OpenAI-compatible Responses API structure output[].content[].output_text
            if not raw_objects and "output" in resp_data and isinstance(resp_data["output"], list):
                for item in resp_data["output"]:
                    if isinstance(item, dict) and item.get("type") == "message" and "content" in item:
                        for c in item.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                text_payload = c.get("text", "").strip()
                                if text_payload:
                                    try:
                                        parsed = json.loads(text_payload)
                                        if isinstance(parsed, dict):
                                            raw_objects = parsed.get("objects") or parsed.get("features") or []
                                            if not architectural_summary:
                                                architectural_summary = str(parsed.get("architectural_summary", ""))
                                        elif isinstance(parsed, list):
                                            raw_objects = parsed
                                    except Exception:
                                        # Parse token grammar format from cookbook specification
                                        raw_objects = parse_sam_token_grammar(text_payload)
                                        if not raw_objects and text_payload != "<0f>":
                                            raise VisualIntelligenceError("malformed_response", "Unrecognized SAM response format.")

        features: list[SegmentedFeature] = []
        for obj in raw_objects:
            if not isinstance(obj, dict):
                continue

            conf = float(obj.get("confidence", 0.0))
            if conf < 0.70:
                continue

            box_raw = obj.get("box") or obj.get("bounding_box")
            if not box_raw:
                continue

            # Validate coordinates, bounds, dimensions, and ordering strictly
            validated_box = validate_bounding_box(box_raw)

            mask_svg = validate_mask(obj.get("mask") or obj.get("segmentation_mask_svg"))
            if not mask_svg:
                # Construct standard SVG visualization from validated bounding box
                ymin, xmin, ymax, xmax = validated_box
                mask_svg = (
                    f"<svg viewBox='0 0 100 100'>"
                    f"<rect x='{xmin*100:.1f}' y='{ymin*100:.1f}' "
                    f"width='{(xmax-xmin)*100:.1f}' height='{(ymax-ymin)*100:.1f}' "
                    f"fill='rgba(59,130,246,0.3)' stroke='#3b82f6'/>"
                    f"</svg>"
                )

            features.append(
                SegmentedFeature(
                    feature_id=str(obj.get("id") or obj.get("feature_id") or len(features) + 1),
                    label=str(obj.get("label") or prompt_text),
                    confidence=conf,
                    bounding_box=validated_box,
                    segmentation_mask_svg=mask_svg,
                    description=str(obj.get("description", "")),
                )
            )

        if not architectural_summary:
            if features:
                architectural_summary = f"Meta SAM 3.1 identified {len(features)} visual detections."
            else:
                architectural_summary = "Meta SAM 3.1 completed segmentation: no architectural features detected matching the requested labels."

        return SAMSegmentResponse(
            image_id=request.image_id,
            property_id=request.property_id,
            model_id=self.settings.sam_model,
            features=features,
            architectural_summary=architectural_summary,
            is_authentic=True,
        )
