"""Smoke tests for the Phase 0 application skeleton."""

from __future__ import annotations

from httpx import AsyncClient

from app.main import MAX_BODY_BYTES, SECURITY_HEADERS


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "memberassist"
    assert body["synthetic_data"] is True


async def test_security_headers_are_present(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value


async def test_config_status_never_returns_secret_values(client: AsyncClient) -> None:
    """The config endpoint reports presence as booleans, never key material."""
    response = await client.get("/api/health/config")
    assert response.status_code == 200
    body = response.json()
    assert set(body["llm_providers_configured"]) == {"gemini", "groq", "openrouter", "ollama"}
    assert all(isinstance(value, bool) for value in body["llm_providers_configured"].values())
    assert isinstance(body["cms_marketplace_key_present"], bool)


async def test_oversized_body_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/health",
        content=b"x",
        headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "text/plain"},
    )
    assert response.status_code == 413


async def test_unknown_route_is_a_clean_404(client: AsyncClient) -> None:
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
