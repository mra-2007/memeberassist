"""NLM Clinical Tables client: ICD-10-CM and HCPCS code lookups.

Real response shape, confirmed by calling the service (see
``data/samples/clinicaltables_*.json``)::

    [ total, [codes...], null, [[code, name], ...] ]

**Coverage limit, verified by probing:** the HCPCS table holds HCPCS Level II
codes only (``A4253``, ``J1745``, ``G0008``). Five-digit CPT codes such as
``73721`` and ``99213`` return zero results, because CPT is licensed by the AMA
and NLM does not redistribute it. Descriptions for CPT codes therefore come
from the CMS Physician Fee Schedule files instead; see
``app/external/cms_prices.py`` and ``docs/data_sources.md``.
"""

from __future__ import annotations

import logging
import re

from app.external.cache import fetch_json

ICD10CM_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"
HCPCS_URL = "https://clinicaltables.nlm.nih.gov/api/hcpcs/v3/search"

#: 12 hours, within NLM's suggested caching window.
TTL_SECONDS = 12 * 60 * 60

# Deliberately permissive: a shape pre-filter, not a validator. ICD-10-CM
# includes U-codes (U07.1 COVID-19) and 7-character codes (T14.8XXA), and the
# authoritative check is the Clinical Tables lookup that follows.
ICD10_PATTERN = re.compile(r"^[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")
HCPCS_LEVEL2_PATTERN = re.compile(r"^[A-V][0-9]{4}$")
CPT_PATTERN = re.compile(r"^[0-9]{5}$")

logger = logging.getLogger(__name__)


def _rows(payload: object) -> list[list[str]]:
    """Pull the ``[[code, name], ...]`` block out of a Clinical Tables reply."""
    if not isinstance(payload, list) or len(payload) < 4:
        return []
    rows = payload[3]
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, list) and len(row) >= 2]


def lookup_icd10(code: str) -> str | None:
    """Return the official ICD-10-CM title for ``code``, or None if unknown.

    The search is a substring match, so the exact code is required in the
    result rather than merely a near miss.
    """
    normalised = code.strip().upper()
    payload = fetch_json(
        ICD10CM_URL,
        {"sf": "code,name", "terms": normalised, "maxList": 20},
        ttl=TTL_SECONDS,
    )
    for row_code, row_name in ((r[0], r[1]) for r in _rows(payload)):
        if row_code.upper() == normalised:
            return row_name
    return None


def search_icd10(term: str, limit: int = 10) -> list[tuple[str, str]]:
    """Search ICD-10-CM by code fragment or words, returning (code, title)."""
    payload = fetch_json(
        ICD10CM_URL,
        {"sf": "code,name", "terms": term, "maxList": limit},
        ttl=TTL_SECONDS,
    )
    return [(row[0], row[1]) for row in _rows(payload)][:limit]


def lookup_hcpcs(code: str) -> str | None:
    """Return the HCPCS Level II short description, or None.

    Returns None for CPT codes by design: the table does not contain them.
    """
    normalised = code.strip().upper()
    payload = fetch_json(HCPCS_URL, {"terms": normalised, "maxList": 20}, ttl=TTL_SECONDS)
    for row_code, row_name in ((r[0], r[1]) for r in _rows(payload)):
        if row_code.upper() == normalised:
            return row_name
    return None


def classify_code(code: str) -> str:
    """Name the code system a string belongs to.

    Returns one of ``icd10cm``, ``hcpcs_level2``, ``cpt`` or ``unknown``.
    Used by the code explainer to pick the right lookup and by the data
    scripts to verify mappings.
    """
    value = code.strip().upper()
    if ICD10_PATTERN.match(value):
        return "icd10cm"
    if HCPCS_LEVEL2_PATTERN.match(value):
        return "hcpcs_level2"
    if CPT_PATTERN.match(value):
        return "cpt"
    return "unknown"


def verify_icd10_codes(codes: list[str]) -> dict[str, str | None]:
    """Verify a batch of ICD-10-CM codes against Clinical Tables.

    Every mapping we build is checked this way rather than trusted, which is
    what project rule 7.6 requires of the SNOMED-to-ICD-10 mapping.
    """
    results: dict[str, str | None] = {}
    for code in codes:
        try:
            results[code] = lookup_icd10(code)
        except Exception as exc:
            logger.warning("ICD-10 verification failed for %s: %s", code, exc)
            results[code] = None
    return results
