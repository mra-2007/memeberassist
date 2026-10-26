"""Health and readiness endpoints.

``/api/health`` is cheap and safe to poll (Docker health checks use it).
``/api/health/models`` gains live model status in Phase 3, when the AI
registry and LLM router exist; until then it reports which providers are
*configured* and says so explicitly rather than implying they are loaded.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter

from app import __version__
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/health", tags=["health"])

_STARTED_AT = time.time()


@router.get("")
async def health() -> dict[str, Any]:
    """Liveness probe. Reveals no secrets and touches no external service."""
    settings: Settings = get_settings()
    return {
        "status": "ok",
        "app": "memberassist",
        "version": __version__,
        "env": settings.app_env,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "synthetic_data": True,
    }


@router.get("/config")
async def config_status() -> dict[str, Any]:
    """Which configuration is present. Reports presence only, never values."""
    settings = get_settings()
    return {
        "llm_providers_configured": settings.configured_providers(),
        "cms_marketplace_key_present": bool(settings.cms_marketplace_api_key),
        "database_backend": "sqlite" if settings.is_sqlite else "postgresql",
        "privacy_mode": settings.privacy_mode,
        "plan_scope": {
            "state": settings.plan_state,
            "county_fips": settings.plan_county_fips,
            "year": settings.plan_year,
        },
        "missing_required": settings.missing_required_keys(),
    }


@router.get("/models")
async def models_status() -> dict[str, Any]:
    """Live AI-stack status.

    Phase 0 has no models loaded yet, so this returns the honest
    ``not_implemented_until_phase_3`` readiness state instead of inventing
    statuses. Phase 3 replaces the body with real registry data.
    """
    settings = get_settings()
    readiness: Literal["not_implemented_until_phase_3"] = "not_implemented_until_phase_3"
    return {
        "readiness": readiness,
        "cloud_providers": settings.configured_providers(),
        "local_models": [],
        "note": "Model loading and live latency arrive with the AI registry in Phase 3.",
    }
