"""Creative Studio service using Meta Muse Image.
Generates marketing creative concepts and image variations grounded in verified property features
while strictly preserving authentic photography and marking concepts with clear authenticity disclaimers.
"""

import base64
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from io import BytesIO
import logging
from typing import Any
from uuid import UUID, uuid4

import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from app.growth_engine.config import GrowthEngineSettings
from app.growth_engine.schemas import (
    CreativeAssetResponse,
    CreativeGenerateRequest,
    PropertyGrounding,
    SegmentedFeature,
)

logger = logging.getLogger(__name__)


class CreativeServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_image_magic_bytes(data: bytes) -> str:
    """Validate image container magic bytes. Returns detected mime format or raises CreativeServiceError."""
    if len(data) < 8:
        raise CreativeServiceError("invalid_image_bytes", "Image payload is too small to be a valid image.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and b"WEBP" in data[8:16]:
        return "image/webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if b"<svg" in data[:1024]:
        return "image/svg+xml"
    raise CreativeServiceError("invalid_image_magic_bytes", "Image payload does not match supported image magic bytes (PNG, JPEG, WebP, SVG).")


class MetaMuseImageService:
    def __init__(
        self,
        settings: GrowthEngineSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self.settings = settings or GrowthEngineSettings()
        self._http_client = http_client
        secret_key = self.settings.model_api_key.get_secret_value().strip() if self.settings.model_api_key else ""
        self._secret_key = secret_key
        self._client = OpenAI(
            api_key=secret_key or "no-key",
            base_url=self.settings.meta_base_url,
            timeout=self.settings.model_timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )

    def generate_creative_asset(
        self,
        request: CreativeGenerateRequest,
        grounding: PropertyGrounding,
        segmented_features: list[SegmentedFeature] | None = None,
        brief_context: dict[str, Any] | None = None,
    ) -> CreativeAssetResponse:
        """Generate a marketing creative concept using Meta Muse Image.
        Maintains source-photo provenance, permanent concept watermark, and honest source attribution.
        """
        # 1. Build prompt strictly grounded in verified property facts
        feature_labels = [f.label for f in (segmented_features or [])]
        features_phrase = f", illustration cues from image analysis: {', '.join(feature_labels[:3])}" if feature_labels else ""

        full_prompt = (
            f"SoloEstate Marketing Creative for {grounding.title} in {escape(grounding.locality)}. "
            f"Style: {request.style}. Aspect ratio: {request.aspect_ratio}. "
            f"Concept type: {request.asset_type}. "
            f"Verified specs: {grounding.bedrooms} BHK residential property{features_phrase}. "
            f"Owner brief: {request.creative_prompt}. "
            f"STRICT GROUNDING RULE: Do not fabricate unverified amenities, swimming pools, or fictional views. "
            f"Clean modern aesthetic, professional real estate marketing layout."
        )

        prompt_hash = sha256(full_prompt.encode("utf-8")).hexdigest()
        asset_id = uuid4()
        image_url = ""
        model_id = self.settings.muse_image_model
        source = "muse_image"

        # 2. Invoke Meta Muse Image endpoint if credentials configured
        if self._secret_key:
            try:
                size_map = {
                    "1:1": "1024x1024",
                    "4:5": "1024x1024",
                    "9:16": "1024x1792",
                    "16:9": "1792x1024",
                }
                res = self._client.images.generate(
                    model=self.settings.muse_image_model,
                    prompt=full_prompt,
                    n=1,
                    response_format="b64_json",
                    size=size_map.get(request.aspect_ratio, "1024x1024"),
                )
                if res and res.data:
                    encoded = getattr(res.data[0], "b64_json", None)
                    if encoded and len(encoded) <= 14_000_000:
                        image_bytes = base64.b64decode(encoded, validate=True)
                        mime = validate_image_magic_bytes(image_bytes)
                        if mime not in {"image/png", "image/jpeg", "image/webp"}:
                            raise ValueError("Expected a raster creative")
                        with Image.open(BytesIO(image_bytes)) as image:
                            if image.width * image.height > 16_000_000 or image.n_frames != 1:
                                raise ValueError("Invalid creative dimensions or frame count")
                            image.thumbnail((1600, 1600))
                            canvas = Image.new("RGB", (max(480, image.width), image.height + 52), "#0f172a")
                            canvas.paste(image.convert("RGB"), ((canvas.width - image.width) // 2, 0))
                            draw = ImageDraw.Draw(canvas)
                            draw.text(
                                (canvas.width // 2, image.height + 26),
                                "AI Creative Concept - Not Actual Property Photography",
                                fill="white", anchor="mm", font=ImageFont.load_default(size=14),
                            )
                            output = BytesIO()
                            canvas.save(output, format="PNG")
                            image_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            except Exception:
                # Fall back to deterministic concept graphic with honest attribution
                pass

        if not image_url:
            model_id = "svg_concept_fallback"
            source = "svg_concept_fallback"
            bg_gradient = "#1e293b" if request.style == "luxury" else "#0f172a"
            accent_color = "#f59e0b" if "festive" in request.creative_prompt.lower() else "#38bdf8"
            svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{bg_gradient}"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
  </defs>
  <rect width="800" height="800" fill="url(#bg)"/>
  <rect x="40" y="40" width="720" height="720" rx="24" fill="none" stroke="{accent_color}" stroke-width="2" stroke-opacity="0.3"/>
  <text x="70" y="110" font-family="system-ui, sans-serif" font-size="28" font-weight="700" fill="{accent_color}">{escape(grounding.title.upper())}</text>
  <text x="70" y="150" font-family="system-ui, sans-serif" font-size="20" fill="#94a3b8">Prime {escape(grounding.locality)} • {grounding.bedrooms} BHK Verified Layout</text>
  <rect x="70" y="190" width="660" height="420" rx="16" fill="#1e293b" stroke="#334155" stroke-width="2"/>
  <text x="400" y="380" font-family="system-ui, sans-serif" font-size="22" font-weight="600" fill="#f8fafc" text-anchor="middle">{escape(request.creative_prompt[:50])}</text>
  <text x="400" y="420" font-family="system-ui, sans-serif" font-size="16" fill="#94a3b8" text-anchor="middle">Style: {escape(request.style.title())} • Concept: {request.asset_type.replace('_', ' ').title()}</text>
  <rect x="70" y="640" width="660" height="80" rx="12" fill="rgba(245, 158, 11, 0.15)" stroke="#f59e0b" stroke-width="1.5"/>
  <text x="400" y="688" font-family="system-ui, sans-serif" font-size="15" font-weight="600" fill="#fbbf24" text-anchor="middle">⚠️ AI Creative Concept — Not Actual Property Photography</text>
</svg>"""
            import urllib.parse
            image_url = f"data:image/svg+xml;utf8,{urllib.parse.quote(svg_content)}"

        return CreativeAssetResponse(
            id=asset_id,
            kit_id=request.kit_id,
            property_id=request.property_id,
            source_image_id=request.source_image_id,
            asset_type=request.asset_type,
            is_authentic=False,
            concept_watermark="AI Creative Concept - Not Actual Property Photography",
            image_url=image_url,
            segmentation_data={"features_referenced": feature_labels, "export_format": "labelled_concept_v1"},
            prompt_text=full_prompt,
            prompt_hash=prompt_hash,
            model_id=model_id,
            source=source,
            status="draft",
            version=1,
            created_at=datetime.now(timezone.utc),
        )
