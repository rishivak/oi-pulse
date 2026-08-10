from __future__ import annotations

from enum import IntEnum
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BucketInterval(IntEnum):
    ONE = 1
    THREE = 3
    FIVE = 5
    TEN = 10
    FIFTEEN = 15
    THIRTY = 30


SUPPORTED_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "SENSEX"}

# Maps underlying symbol → Upstox instrument_key for the index feed
UNDERLYING_INSTRUMENT_KEYS: dict[str, str] = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"

    # Database
    database_url: str
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Token encryption — Fernet key (URL-safe base64-encoded 32 bytes)
    token_encryption_key: str

    # Session cookies
    session_secret_key: str
    session_ttl_seconds: int = 86400

    # Upstox OAuth
    upstox_client_id: str
    upstox_client_secret: str
    upstox_redirect_uri: str
    upstox_api_base: str = "https://api.upstox.com/v2"
    upstox_auth_base: str = "https://api.upstox.com/v2/login/authorization"

    # Collector
    default_interval_min: int = 5
    collector_underlyings: str = "NIFTY,BANKNIFTY,SENSEX"
    market_tz: str = "Asia/Kolkata"

    # CORS
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_underlyings(self) -> list[str]:
        return [u.strip().upper() for u in self.collector_underlyings.split(",") if u.strip()]

    @field_validator("token_encryption_key")
    @classmethod
    def _check_fernet_key(cls, v: str) -> str:
        if v == "REPLACE_WITH_FERNET_KEY":
            raise ValueError("TOKEN_ENCRYPTION_KEY must be set to a real Fernet key")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
