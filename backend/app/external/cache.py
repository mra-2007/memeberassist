"""Shared HTTP client for public health-data APIs.

Three things every external call needs, in one place:

* **A disk cache**, so repeated runs of the fetch scripts and repeated member
  questions do not hammer public services. NLM asks for at most 100 requests
  per minute per IP and caching for 12-24 hours; this honours that.
* **A polite User-Agent with a contact address**, which NLM and CMS both ask
  for.
* **Retries with backoff** on transient failures only, never on a 4xx.

No module here may call an LLM: these are data APIs. ``tests/test_architecture.py``
enforces that.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx
from diskcache import Cache
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app import __version__
from app.config import DATA_DIR, get_settings

logger = logging.getLogger(__name__)

#: 24 hours. External reference data does not change within a working day.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

_cache: Cache | None = None


def get_cache() -> Cache:
    """Process-wide disk cache under ``data/cache`` (git-ignored)."""
    global _cache
    if _cache is None:
        path = DATA_DIR / "cache" / "http"
        path.mkdir(parents=True, exist_ok=True)
        _cache = Cache(str(path), size_limit=512 * 1024 * 1024)
    return _cache


def user_agent() -> str:
    """Polite identifying User-Agent, with a contact address when configured."""
    contact = get_settings().contact_email
    suffix = f"; {contact}" if contact else ""
    return f"MemberAssist/{__version__} (CBIT student project{suffix})"


def default_headers() -> dict[str, str]:
    """Headers sent on every outbound data request."""
    return {"User-Agent": user_agent(), "Accept": "application/json"}


def _cache_key(method: str, url: str, params: dict[str, Any] | None) -> str:
    """Stable key for a request, independent of parameter ordering."""
    canonical = json.dumps(params or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{method}|{url}|{canonical}".encode()).hexdigest()
    return f"http:{digest}"


class ExternalApiError(RuntimeError):
    """An external data API returned an error we cannot work around."""

    def __init__(self, url: str, status: int, body: str) -> None:
        super().__init__(f"{url} returned HTTP {status}: {body[:300]}")
        self.url = url
        self.status = status
        self.body = body


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _request(
    client: httpx.Client, method: str, url: str, params: dict[str, Any] | None
) -> httpx.Response:
    """One HTTP attempt, retried only on timeouts and transport errors."""
    return client.request(method, url, params=params)


def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    ttl: int = DEFAULT_TTL_SECONDS,
    timeout: float = 30.0,
    use_cache: bool = True,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET ``url`` and return parsed JSON, caching the decoded result.

    Raises:
        ExternalApiError: when the service answers with a non-2xx status.
    """
    key = _cache_key("GET", url, params)
    cache = get_cache()
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    merged = {**default_headers(), **(headers or {})}
    with httpx.Client(timeout=timeout, headers=merged, follow_redirects=True) as client:
        response = _request(client, "GET", url, params)
    if response.status_code >= 400:
        raise ExternalApiError(url, response.status_code, response.text)

    payload = response.json()
    if use_cache:
        cache.set(key, payload, expire=ttl)
    return payload


def fetch_bytes(
    url: str,
    *,
    timeout: float = 600.0,
    headers: dict[str, str] | None = None,
    cache_to_raw: bool = True,
) -> bytes:
    """Download a binary file (a PUF zip, an RVU zip, an HTML page).

    Large archives are kept on disk under ``data/raw`` rather than in the
    key-value cache, so re-running the pipeline does not re-download hundreds
    of megabytes from CMS. ``data/raw`` is git-ignored, which satisfies the
    rule that raw downloads are never committed.
    """
    raw_path = DATA_DIR / "raw" / _raw_filename(url)
    if cache_to_raw and raw_path.exists():
        logger.info(
            "using cached %s (%.1f MB)", raw_path.name, raw_path.stat().st_size / 1024 / 1024
        )
        return raw_path.read_bytes()

    merged = {**default_headers(), "Accept": "*/*", **(headers or {})}
    chunks: list[bytes] = []
    with (
        httpx.Client(timeout=timeout, headers=merged, follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        if response.status_code >= 400:
            response.read()
            raise ExternalApiError(url, response.status_code, response.text)
        chunks.extend(response.iter_bytes(chunk_size=1 << 16))
    payload = b"".join(chunks)
    logger.info("downloaded %s (%.1f MB)", url, len(payload) / 1024 / 1024)

    if cache_to_raw:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(payload)
    return payload


def _raw_filename(url: str) -> str:
    """A stable, readable filename for a cached download."""
    tail = url.rstrip("/").rsplit("/", 1)[-1][:60] or "download"
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in tail)
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    return f"{digest}_{safe}"


def save_sample(name: str, payload: Any) -> None:
    """Write a small sample response to ``data/samples`` for reference.

    Project rule 2.1.8: every external API is called once and its real response
    shape recorded, so code is written against reality rather than assumption.
    """
    directory = DATA_DIR / "samples"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    text = json.dumps(payload, indent=1, default=str)
    target.write_text(text[:200_000], encoding="utf-8")
