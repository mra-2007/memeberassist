"""MedlinePlus Connect client: plain-language consumer health summaries.

Real response shape, confirmed by calling the service (see
``data/samples/medlineplus_icd10.json``)::

    {"feed": {"entry": [{"title": {"_value": ...},
                         "link":  [{"href": ...}],
                         "summary": {"_value": "<html>"}}]}}

The summary arrives as HTML. It is stripped to plain text here, because the
output guard blocks links and images from outside the source allowlist and
because the LLM should receive data, not markup it might be tempted to echo.
"""

from __future__ import annotations

import html
import re

from app.external.cache import fetch_json

SERVICE_URL = "https://connect.medlineplus.gov/service"

#: Code-system OIDs MedlinePlus Connect understands.
ICD10CM_OID = "2.16.840.1.113883.6.90"
RXNORM_OID = "2.16.840.1.113883.6.88"

#: 24 hours, the upper end of NLM's suggested caching window.
TTL_SECONDS = 24 * 60 * 60

_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _to_text(markup: str) -> str:
    """Turn a MedlinePlus HTML summary into a single plain-text paragraph."""
    without_tags = _TAG.sub(" ", markup)
    return _WHITESPACE.sub(" ", html.unescape(without_tags)).strip()


def _first_entry(payload: object) -> dict | None:
    """The first entry of a MedlinePlus feed, or None when nothing matched."""
    if not isinstance(payload, dict):
        return None
    entries = payload.get("feed", {}).get("entry")
    if isinstance(entries, dict):
        return entries
    if isinstance(entries, list) and entries:
        first = entries[0]
        return first if isinstance(first, dict) else None
    return None


def _value(node: object) -> str:
    """Read MedlinePlus's ``{"_value": ...}`` wrapper."""
    if isinstance(node, dict):
        return str(node.get("_value", ""))
    return str(node or "")


def explain_code(code: str, code_system: str = ICD10CM_OID, *, max_chars: int = 600) -> dict | None:
    """Return a plain-language summary for a clinical code.

    Args:
        code: The code to explain, e.g. ``M17.11``.
        code_system: An OID from this module (ICD-10-CM or RxNorm).
        max_chars: Truncation limit for the summary text.

    Returns:
        ``{"title", "summary", "url"}`` or None when MedlinePlus has no entry.
    """
    payload = fetch_json(
        SERVICE_URL,
        {
            "mainSearchCriteria.v.cs": code_system,
            "mainSearchCriteria.v.c": code.strip(),
            "knowledgeResponseType": "application/json",
            "informationRecipient.languageCode.c": "en",
        },
        ttl=TTL_SECONDS,
    )
    entry = _first_entry(payload)
    if entry is None:
        return None

    links = entry.get("link")
    if isinstance(links, dict):
        links = [links]
    url = ""
    if isinstance(links, list) and links:
        first_link = links[0]
        if isinstance(first_link, dict):
            url = str(first_link.get("href", ""))

    summary = _to_text(_value(entry.get("summary")))
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "…"

    title = _value(entry.get("title"))
    if not title and not summary:
        return None
    return {"title": title, "summary": summary, "url": url}


def explain_rxnorm(rxcui: str, *, max_chars: int = 600) -> dict | None:
    """Plain-language summary for an RxNorm concept (Phase 15)."""
    return explain_code(rxcui, RXNORM_OID, max_chars=max_chars)
