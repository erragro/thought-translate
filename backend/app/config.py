"""
app/config.py
=============
Centralised configuration via Pydantic BaseSettings. All env vars are
declared here; modules import the `settings` singleton.

Ported from kirana_kart_final's app/config.py — trimmed to what
Thought Translate actually needs (no Weaviate, Celery, OTel, PII
field-encryption, SMTP — add back if/when those features exist).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# pydantic-settings' env_file= below loads .env into the Settings object,
# but some libraries (Google auth, etc., in future) read env vars straight
# from os.environ — load .env into the real process environment too.
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ============================================================
    # DATABASE
    # ============================================================

    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5434, alias="DB_PORT")
    db_name: str = Field(default="thought_translate", alias="DB_NAME")
    db_user: str = Field(default="tt_user", alias="DB_USER")
    db_password: str = Field(default="tt_password", alias="DB_PASSWORD")

    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=3600, alias="DB_POOL_RECYCLE")

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ============================================================
    # REDIS (rate limiting + login lockout counters)
    # ============================================================

    redis_url: str = Field(default="redis://localhost:6380/0", alias="REDIS_URL")
    redis_max_connections: int = Field(default=20, alias="REDIS_MAX_CONNECTIONS")

    # ============================================================
    # AUTHENTICATION — JWT
    # ============================================================

    jwt_secret_key: str = Field(default="CHANGE_ME_DEV_ONLY", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_expire_minutes: int = Field(default=60, alias="JWT_ACCESS_EXPIRE_MINUTES")
    jwt_refresh_expire_days: int = Field(default=30, alias="JWT_REFRESH_EXPIRE_DAYS")

    # Bootstrap admin created on first startup (if no users exist)
    bootstrap_admin_email: str = Field(default="", alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str = Field(default="", alias="BOOTSTRAP_ADMIN_PASSWORD")
    bootstrap_admin_name: str = Field(default="Admin", alias="BOOTSTRAP_ADMIN_NAME")

    # OAuth: backend callback base URL and frontend URL
    oauth_redirect_base_url: str = Field(default="http://localhost:8010", alias="OAUTH_REDIRECT_BASE_URL")
    frontend_url: str = Field(default="http://localhost:5174", alias="FRONTEND_URL")

    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")

    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")

    microsoft_client_id: str = Field(default="", alias="MICROSOFT_CLIENT_ID")
    microsoft_client_secret: str = Field(default="", alias="MICROSOFT_CLIENT_SECRET")

    # ============================================================
    # LLM PROVIDERS — Gemini via Vertex AI (en + Hindi), Sarvam AI
    # (other Indian languages). Same split as quickbites-bot: routing is
    # by language, not a manual toggle.
    # ============================================================

    # Vertex AI auth is standard Google Application Default Credentials —
    # GOOGLE_APPLICATION_CREDENTIALS (service-account key file) or
    # `gcloud auth application-default login` — the google-genai SDK
    # reads it directly from the environment, not from this Settings object.
    google_cloud_project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(default="asia-south1", alias="GOOGLE_CLOUD_LOCATION")
    gemini_fast_model: str = Field(default="gemini-2.5-flash-lite", alias="GEMINI_FAST_MODEL")
    gemini_smart_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_SMART_MODEL")

    sarvam_api_key: str = Field(default="", alias="SARVAM_API_KEY")
    # sarvam-30b was deprecated (confirmed 2026-08-03 — the API now
    # rejects it and points to sarvam-105b as the only chat-completions
    # model). Both roles point at it for now; it's a reasoning model that
    # burns ~1500+ completion tokens on chain-of-thought before any
    # answer, so it's NOT used for the core translate call — see
    # sarvam_client.py's dedicated /translate wrapper for that instead.
    sarvam_fast_model: str = Field(default="sarvam-105b", alias="SARVAM_FAST_MODEL")
    sarvam_smart_model: str = Field(default="sarvam-105b", alias="SARVAM_SMART_MODEL")
    # Register for the dedicated /translate endpoint. Options (confirmed
    # via the API's own validation error): formal | modern-colloquial |
    # classic-colloquial | code-mixed. "formal" tested clean — common,
    # correct-grammar Hindi with no archaic terms; modern-colloquial
    # leaves English words untranslated inline (e.g. "umbrella", "better"
    # kept as-is), which fails the "correct target-language grammar"
    # requirement, so it's deliberately not the default.
    sarvam_translate_mode: str = Field(default="formal", alias="SARVAM_TRANSLATE_MODE")
    # How long a cached translation is served before re-calling Sarvam.
    translate_cache_ttl_seconds: int = Field(default=2592000, alias="TRANSLATE_CACHE_TTL_SECONDS")  # 30 days

    # ============================================================
    # CORS
    # ============================================================

    cors_origins: str = Field(default="http://localhost:5174", alias="CORS_ORIGINS")

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ============================================================
    # DEPLOYMENT
    # ============================================================

    deployment_env: str = Field(default="development", alias="DEPLOYMENT_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ============================================================
    # VALIDATION
    # ============================================================

    @model_validator(mode="after")
    def enforce_production_secrets(self) -> "Settings":
        """Refuse to start in production with placeholder secrets."""
        if self.deployment_env == "production":
            if self.jwt_secret_key == "CHANGE_ME_DEV_ONLY" or len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be a strong random secret in production. "
                    'Generate one with: python -c "import secrets; print(secrets.token_hex(64))"'
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
