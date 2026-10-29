"""Environment and path configuration without embedded credentials."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables and an optional local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="QUANT_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "development"
    data_api_base_url: str | None = None
    data_api_key: SecretStr | None = None
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    def safe_summary(self) -> dict[str, object]:
        """Return log-safe settings without exposing secret values."""
        return {
            "environment": self.environment,
            "project_root": str(self.project_root),
            "data_api_configured": bool(self.data_api_base_url and self.data_api_key),
            "http_timeout_seconds": self.http_timeout_seconds,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()

