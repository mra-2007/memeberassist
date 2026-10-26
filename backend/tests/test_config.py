"""Tests for configuration parsing and the derived helpers."""

from __future__ import annotations

from typing import Any

from app.config import Settings


def _settings(**overrides: Any) -> Settings:
    """Build Settings from explicit values, ignoring any local .env file."""
    defaults: dict[str, Any] = {
        "_env_file": None,
        "jwt_secret": "test-secret",
        "allowed_origins": "http://localhost:5173",
    }
    return Settings(**{**defaults, **overrides})


def test_origins_are_split_and_stripped() -> None:
    settings = _settings(allowed_origins="http://a.test , http://b.test ,")
    assert settings.origins == ["http://a.test", "http://b.test"]


def test_provider_chains_are_ordered_lists() -> None:
    settings = _settings(main_llm_order="gemini, groq ,openrouter", aux_llm_order="groq_fast")
    assert settings.main_chain == ["gemini", "groq", "openrouter"]
    assert settings.aux_chain == ["groq_fast"]


def test_configured_providers_reports_presence_only() -> None:
    settings = _settings(gemini_api_key="abc", groq_api_key="")
    providers = settings.configured_providers()
    assert providers["gemini"] is True
    assert providers["groq"] is False


def test_missing_required_keys_flags_absent_jwt_secret_and_llm() -> None:
    settings = _settings(jwt_secret="", gemini_api_key="", groq_api_key="", ollama_model="")
    missing = settings.missing_required_keys()
    assert "JWT_SECRET" in missing
    assert any("GEMINI_API_KEY" in item for item in missing)


def test_a_single_llm_provider_satisfies_the_requirement() -> None:
    settings = _settings(groq_api_key="gsk-test")
    assert settings.missing_required_keys() == []


def test_blank_plan_year_falls_back_to_the_default() -> None:
    assert _settings(plan_year="").plan_year == 2026


def test_sqlite_fallback_is_detected() -> None:
    assert _settings(database_url="sqlite+pysqlite:///./dev.sqlite3").is_sqlite is True
    assert _settings(database_url="postgresql+psycopg://u:p@h/db").is_sqlite is False
