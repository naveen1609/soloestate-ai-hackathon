from pathlib import Path
from typing import Literal

from dotenv.parser import parse_stream
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentFileError(ValueError):
    """A safe diagnostic that includes a line number, never its secret content."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
        hide_input_in_errors=True,
        env_file_encoding="utf-8-sig",
    )

    def __init__(self, **values):
        env_files = values.get("_env_file", self.model_config.get("env_file"))
        if env_files is not None:
            paths = env_files if isinstance(env_files, (list, tuple)) else [env_files]
            for path in paths:
                if Path(path).is_file():
                    with Path(path).open(encoding="utf-8-sig") as stream:
                        for binding in parse_stream(stream):
                            if binding.error:
                                raise EnvironmentFileError(
                                    f"Invalid .env syntax at line {binding.original.line}; check matching quotes. No credentials were displayed."
                                )
        super().__init__(**values)

    app_name: str = "SoloEstate AI"
    model_api_key: SecretStr | None = None
    model_name: str = ""
    model_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    database_sslmode: Literal["verify-full", "require", "disable"] = "verify-full"
    database_sslrootcert: str | None = None
    agent_max_turns: int = Field(default=12, ge=2, le=30)
    agent_max_tool_calls: int = Field(default=16, ge=1, le=50)
    agent_max_failures: int = Field(default=3, ge=1, le=5)
    agent_timeout_seconds: float = Field(default=180, ge=10, le=600, allow_inf_nan=False)
    owner_api_token: SecretStr | None = None
