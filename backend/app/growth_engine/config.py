"""Growth Engine-lite configuration and settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class GrowthEngineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    app_name: str = "SoloEstate AI Growth Engine-lite"
    model_api_key: SecretStr | None = None
    model_name: str = "muse-spark-1.3"
    model_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    # Meta provider endpoint
    meta_base_url: str = "https://api.meta.ai/v1"

    # Specific Meta Model identifiers from api.meta.ai catalog
    muse_spark_model: str = "muse-spark-1.3"
    muse_voice_model: str = "muse-voice-transcribe-1.0"
    sam_model: str = "sam-3.1"
    muse_image_model: str = "muse-image-1.0"
    muse_wonder_model: str = "muse-wonder-1.0"

    # Budget & campaign guardrails (MVP constraints)
    max_daily_budget_inr: float = 10000.0
    min_daily_budget_inr: float = 500.0
    max_campaign_duration_days: int = 90
    min_campaign_duration_days: int = 3
