"""NPI Registry client: real providers by specialty and city.

Real response shape, confirmed by calling the service (see
``data/samples/npi_registry.json``)::

    {"result_count": n,
     "results": [{"number": "1699847400",
                  "enumeration_type": "NPI-1",
                  "basic": {"first_name", "last_name", "credential",
                            "organization_name", "status"},
                  "addresses": [{"address_purpose": "LOCATION"|"MAILING",
                                 "address_1", "city", "state", "postal_code",
                                 "telephone_number"}],
                  "taxonomies": [{"code", "desc", "primary", "state"}]}]}

**A trap the probe exposed:** the registry's ``city`` filter matches *any*
address on the record, including the mailing address. Querying Dallas, TX
returned a surgeon whose practice is in Minneapolis but whose billing address
is a Dallas PO box. Every result is therefore re-checked against its
``LOCATION`` address before being accepted.
"""

from __future__ import annotations

import logging
from typing import Any

from app.external.cache import fetch_json

API_URL = "https://npiregistry.cms.hhs.gov/api/"
API_VERSION = "2.1"

#: Providers change slowly; a week of caching is still honest.
TTL_SECONDS = 7 * 24 * 60 * 60

#: Registry hard limit per request.
MAX_LIMIT = 200

logger = logging.getLogger(__name__)


def _location_address(record: dict[str, Any]) -> dict[str, Any] | None:
    """The practice (LOCATION) address, which is where care is actually given."""
    for address in record.get("addresses") or []:
        if isinstance(address, dict) and address.get("address_purpose") == "LOCATION":
            return address
    return None


def _primary_taxonomy(record: dict[str, Any]) -> dict[str, Any] | None:
    """The provider's primary taxonomy, falling back to the first listed."""
    taxonomies = [t for t in (record.get("taxonomies") or []) if isinstance(t, dict)]
    for taxonomy in taxonomies:
        if taxonomy.get("primary"):
            return taxonomy
    return taxonomies[0] if taxonomies else None


def _matching_taxonomy(record: dict[str, Any], search_term: str) -> dict[str, Any] | None:
    """The taxonomy on this record that actually matches the search term.

    The registry's ``taxonomy_description`` filter matches *any* taxonomy on a
    record, and a provider's **primary** taxonomy is often a different
    speciality. Reporting the primary one would mislabel the result -- a
    Radiology search returned a record whose primary taxonomy is Neurology --
    so the matching taxonomy is preferred and the primary is only a fallback.
    """
    needle = search_term.strip().lower()
    taxonomies = [t for t in (record.get("taxonomies") or []) if isinstance(t, dict)]
    matches = [t for t in taxonomies if needle in str(t.get("desc", "")).lower()]
    for taxonomy in matches:
        if taxonomy.get("primary"):
            return taxonomy
    if matches:
        return matches[0]
    return _primary_taxonomy(record)


def _display_name(record: dict[str, Any]) -> str:
    """Person name for NPI-1 records, organisation name for NPI-2."""
    basic = record.get("basic") or {}
    organisation = basic.get("organization_name")
    if organisation:
        return str(organisation).title()
    parts = [basic.get("first_name", ""), basic.get("last_name", "")]
    name = " ".join(p for p in parts if p).title()
    credential = str(basic.get("credential") or "").strip(" -")
    return f"{name}, {credential}" if credential and name else name


def _clean_phone(value: object) -> str | None:
    """Keep phone numbers as the registry publishes them, or drop them."""
    text = str(value or "").strip()
    return text or None


class ProviderRecord(dict):
    """A validated provider row ready for the ``providers`` table."""


def search_providers(
    taxonomy_description: str,
    city: str,
    state: str,
    *,
    limit: int = 50,
    enumeration_type: str | None = "NPI-1",
) -> list[ProviderRecord]:
    """Find providers of one specialty whose practice address is in ``city``.

    Args:
        taxonomy_description: An NPI taxonomy description, e.g. ``Orthopaedic
            Surgery``. The registry matches it as a prefix, so broad terms
            return sub-specialties too.
        city: Practice city, matched against the LOCATION address.
        state: Two-letter state code.
        limit: How many registry records to request (capped at 200).
        enumeration_type: ``NPI-1`` for individuals, ``NPI-2`` for
            organisations, None for both.

    Returns:
        Provider rows whose practice address really is in the requested city
        and state. Records that only have a mailing address there are dropped.
    """
    params: dict[str, Any] = {
        "version": API_VERSION,
        "taxonomy_description": taxonomy_description,
        "city": city,
        "state": state.upper(),
        "limit": min(limit, MAX_LIMIT),
    }
    if enumeration_type:
        params["enumeration_type"] = enumeration_type

    payload = fetch_json(API_URL, params, ttl=TTL_SECONDS)
    if not isinstance(payload, dict):
        return []
    if "Errors" in payload:
        logger.warning(
            "NPI registry returned errors for %s: %s", taxonomy_description, payload["Errors"]
        )
        return []

    accepted: list[ProviderRecord] = []
    for record in payload.get("results") or []:
        if not isinstance(record, dict):
            continue
        if str(record.get("basic", {}).get("status", "A")).upper() != "A":
            continue  # deactivated NPI

        address = _location_address(record)
        if address is None:
            continue
        if str(address.get("city", "")).upper() != city.upper():
            continue  # mailing address matched, practice address did not
        if str(address.get("state", "")).upper() != state.upper():
            continue

        taxonomy = _matching_taxonomy(record, taxonomy_description) or {}
        if taxonomy_description.strip().lower() not in str(taxonomy.get("desc", "")).lower():
            continue  # the record matched on an unrelated taxonomy
        name = _display_name(record)
        if not name:
            continue

        postal = str(address.get("postal_code") or "")
        accepted.append(
            ProviderRecord(
                npi=str(record.get("number")),
                name=name,
                specialty=str(taxonomy.get("desc") or taxonomy_description),
                specialty_group=taxonomy_description,
                taxonomy_code=str(taxonomy.get("code") or "") or None,
                address=str(address.get("address_1") or "").title(),
                city=str(address.get("city") or "").title(),
                state=str(address.get("state") or "").upper(),
                zip_code=postal[:5],
                phone=_clean_phone(address.get("telephone_number")),
            )
        )
    logger.info(
        "NPI %s in %s, %s: %d returned, %d with a matching practice address",
        taxonomy_description,
        city,
        state,
        payload.get("result_count", 0),
        len(accepted),
    )
    return accepted


def lookup_npi(npi: str) -> ProviderRecord | None:
    """Look up a single NPI number."""
    payload = fetch_json(API_URL, {"version": API_VERSION, "number": npi.strip()}, ttl=TTL_SECONDS)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return None
    record = results[0]
    address = _location_address(record) or {}
    taxonomy = _primary_taxonomy(record) or {}
    postal = str(address.get("postal_code") or "")
    return ProviderRecord(
        npi=str(record.get("number")),
        name=_display_name(record),
        specialty=str(taxonomy.get("desc") or ""),
        taxonomy_code=str(taxonomy.get("code") or "") or None,
        address=str(address.get("address_1") or "").title(),
        city=str(address.get("city") or "").title(),
        state=str(address.get("state") or "").upper(),
        zip_code=postal[:5],
        phone=_clean_phone(address.get("telephone_number")),
    )
