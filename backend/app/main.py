"""FastAPI application factory for MemberAssist.

Routers are mounted here as phases add them. The security middleware
(CORS allowlist, security headers, body-size cap) is wired from Phase 0 so
later phases inherit it rather than bolting it on.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import health
from app.config import get_settings

logger = logging.getLogger("memberassist")

# Responses are JSON or SSE only; a strict CSP costs nothing and blocks
# markdown/HTML injection tricks if a response is ever rendered directly.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

MAX_BODY_BYTES = 2 * 1024 * 1024


def _apply_telemetry_opt_out() -> None:
    """Turn off Hugging Face and Chroma telemetry before any model import."""
    settings = get_settings()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", settings.hf_hub_disable_telemetry)
    os.environ.setdefault("ANONYMIZED_TELEMETRY", settings.anonymized_telemetry)
    os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "none")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start-up and shut-down work (local model warm-up lands here in Phase 3)."""
    settings = get_settings()
    _apply_telemetry_opt_out()
    logging.basicConfig(level=settings.log_level.upper())
    missing = settings.missing_required_keys()
    if missing:
        logger.warning("Starting with missing configuration: %s", ", ".join(missing))
    logger.info(
        "MemberAssist %s starting (env=%s, db=%s)",
        __version__,
        settings.app_env,
        "sqlite" if settings.is_sqlite else "postgresql",
    )
    yield
    logger.info("MemberAssist shutting down")


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="MemberAssist API",
        version=__version__,
        description=(
            "Secure AI assistant for health-insurance members. "
            "All member data in this deployment is synthetic."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Reject oversized bodies and stamp security headers on every response."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": "Request body too large."},
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    app.include_router(health.router)

    @app.get("/api", tags=["meta"])
    async def api_root() -> dict[str, str]:
        """Minimal index so a bare /api call is not a 404."""
        return {"app": "memberassist", "version": __version__, "docs": "/api/docs"}

    return app


app = create_app()
