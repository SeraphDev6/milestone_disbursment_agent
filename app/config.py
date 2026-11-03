from enum import Enum
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    mock = "mock"  # no keys needed: fake PayPal ids, canned verifier
    sandbox = "sandbox"  # real PayPal sandbox and a real model


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mode: Mode = Mode.mock
    database_url: str = "sqlite:///./local.db"
    paypal_client_id: str | None = None
    paypal_client_secret: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, v: str) -> str:
        # Render and most hosts hand out postgres:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

    @model_validator(mode="after")
    def _sandbox_needs_keys(self) -> "Settings":
        if self.mode is Mode.sandbox and not (self.paypal_client_id and self.paypal_client_secret):
            raise ValueError("MODE=sandbox requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
