"""Application configuration.

Every setting comes from the repository-root ``.env`` file (git-ignored) or the
process environment. Nothing secret is ever hard-coded here, and model IDs are
configuration rather than constants so a retired provider model can be swapped
by re-running ``manage.py check-models``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
EVAL_DIR = REPO_ROOT / "eval"
DOCS_DIR = REPO_ROOT / "docs"


class Settings(BaseSettings):
    """Typed view of the ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- environment ------------------------------------------------------
    app_env: str = "development"
    log_level: str = "INFO"

    # ---- LLM providers ----------------------------------------------------
    gemini_api_key: str = ""
    gemini_main_model: str = ""
    gemini_lite_model: str = ""
    groq_api_key: str = ""
    groq_main_model: str = ""
    groq_fast_model: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = ""
    privacy_mode: bool = False
    main_llm_order: str = "gemini,groq,openrouter"
    aux_llm_order: str = "groq_fast,gemini_lite"

    # ---- health data APIs -------------------------------------------------
    cms_marketplace_api_key: str = ""
    openfda_api_key: str = ""
    contact_email: str = ""
    plan_state: str = "TX"
    plan_county_fips: str = "48113"
    plan_year: int = 2026

    # ---- app security -----------------------------------------------------
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60
    rate_limit_per_minute: int = 20
    allowed_origins: str = "http://localhost:5173"
    database_url: str = "sqlite+pysqlite:///./memberassist.sqlite3"
    postgres_password: str = ""
    demo_password: str = ""

    # ---- safety text ------------------------------------------------------
    emergency_text: str = (
        "If this is an emergency, call 911 (US) or 112 (India), "
        "or go to the nearest emergency department now."
    )
    crisis_text: str = (
        "You don't have to go through this alone. In the US call or text 988; "
        "in India call Tele-MANAS at 14416."
    )

    # ---- telemetry --------------------------------------------------------
    hf_hub_disable_telemetry: str = "1"
    anonymized_telemetry: str = "False"

    # ---- limits -----------------------------------------------------------
    max_message_chars: int = 1000
    max_upload_bytes: int = 5 * 1024 * 1024
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2

    @field_validator("plan_year", mode="before")
    @classmethod
    def _blank_year_is_default(cls, value: object) -> object:
        """Treat an empty ``PLAN_YEAR=`` in .env as "use the default"."""
        if isinstance(value, str) and not value.strip():
            return 2026
        return value

    # ---- derived ----------------------------------------------------------
    @property
    def origins(self) -> list[str]:
        """CORS allowlist as a list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def main_chain(self) -> list[str]:
        """Provider fallback order for main reasoning calls."""
        return [p.strip() for p in self.main_llm_order.split(",") if p.strip()]

    @property
    def aux_chain(self) -> list[str]:
        """Provider fallback order for auxiliary (cheap/fast) calls."""
        return [p.strip() for p in self.aux_llm_order.split(",") if p.strip()]

    @property
    def is_sqlite(self) -> bool:
        """True when running on the temporary SQLite fallback."""
        return self.database_url.startswith("sqlite")

    @property
    def resolved_database_url(self) -> str:
        """``DATABASE_URL`` with a relative SQLite path anchored to the repo root.

        Scripts run from the repository root and tests from ``backend/``; without
        this they would silently create two different database files.
        """
        prefix = "sqlite+pysqlite:///"
        if not self.database_url.startswith(prefix):
            return self.database_url
        path = self.database_url[len(prefix) :]
        if path.startswith((":memory:", "/")) or Path(path).is_absolute():
            return self.database_url
        absolute = (REPO_ROOT / path.lstrip("./")).resolve()
        absolute.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{absolute.as_posix()}"

    def configured_providers(self) -> dict[str, bool]:
        """Which LLM providers have a key present (not whether the key works)."""
        return {
            "gemini": bool(self.gemini_api_key),
            "groq": bool(self.groq_api_key),
            "openrouter": bool(self.openrouter_api_key),
            "ollama": bool(self.ollama_model),
        }

    def missing_required_keys(self) -> list[str]:
        """Secrets the app cannot run a full chat flow without."""
        missing: list[str] = []
        if not self.jwt_secret:
            missing.append("JWT_SECRET")
        if not (self.gemini_api_key or self.groq_api_key or self.ollama_model):
            missing.append("GEMINI_API_KEY or GROQ_API_KEY (at least one LLM provider)")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object (parsed once)."""
    return Settings()
