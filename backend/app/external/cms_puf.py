"""Marketplace plan data from the CMS Exchange Public Use Files.

Why the PUFs rather than the Marketplace API
--------------------------------------------
The specification prefers ``https://marketplace.api.healthcare.gov/api/v1``,
and allows the Public Use Files when the key has not arrived. Calling the API
without a key returns, verbatim::

    401 {"message": "No API key found in request", "request_id": "..."}

so the PUFs are the path in use. They need no key, they are the same
regulatory filings the API serves, and the files below were downloaded and
inspected before a line of this parser was written.

The three files used, for plan year ``PLAN_YEAR``
-------------------------------------------------
``service-area-puf``
    8,820 rows. Maps ``ServiceAreaId`` to counties by FIPS code, or flags
    ``CoverEntireState``. Used to keep only plans sold in our county.

``plan-attributes-puf``
    22,059 rows, 151 columns. One row per plan *variant*. Supplies issuer,
    metal level, plan type, deductibles, out-of-pocket maximums, HSA
    eligibility and -- valuable for the Phase 2 knowledge base -- the real
    ``URLForSummaryofBenefitsCoverage`` PDF link.

``benefits-and-cost-sharing-puf``
    1,457,952 rows uncompressed to 375 MB. One row per plan per benefit, with
    copay and coinsurance as human-readable strings. Streamed from inside the
    zip and never written to disk, as required.

Deductibles come from the ``TEHB*`` (total essential health benefits) columns
when medical and drug accumulators are integrated, and from ``MEHB*``
(medical only) otherwise, which is what the plan's own SBC shows a member.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.external.cache import fetch_bytes

PUF_BASE_URL = "https://download.cms.gov/marketplace-puf"
PUF_LANDING_PAGE = "https://www.cms.gov/marketplace/resources/data/public-use-files"
MARKETPLACE_API_BASE = "https://marketplace.api.healthcare.gov/api/v1"

#: Benefit rows we never want: dental-only plans and non-individual markets.
INDIVIDUAL_MARKET = "Individual"

#: CSR variants. ``-00`` is the off-exchange standard plan and ``-01`` the
#: on-exchange standard plan; ``-02`` upward are cost-sharing-reduction
#: variants for subsidised enrollees, which are not the plan a member is
#: normally quoted.
STANDARD_VARIANT_SUFFIXES = ("-01", "-00")

logger = logging.getLogger(__name__)

_MONEY = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
#: CMS writes both "after deductible" and "with deductible"; both mean the
#: deductible applies before this cost sharing does.
_DEDUCTIBLE_APPLIES = re.compile(r"\b(?:after|with)\s+deductible\b", re.IGNORECASE)
_NOT_APPLICABLE = {"", "not applicable", "n/a"}


@dataclass(frozen=True, slots=True)
class CostShare:
    """Parsed cost sharing for one benefit, one plan, one network tier."""

    copay: Decimal | None
    coinsurance: Decimal | None
    after_deductible: bool
    #: CMS's own wording, kept verbatim for display and for citation.
    display_text: str
    #: e.g. "per Stay" when the copay is per admission rather than per visit.
    qualifier: str | None = None
    is_covered: bool = True

    @property
    def is_no_charge(self) -> bool:
        """True when the plan covers the service in full."""
        return self.copay == 0 and self.coinsurance == 0


@dataclass(slots=True)
class PlanRecord:
    """A plan and everything the database needs about it."""

    plan_id: str
    hios_id: str
    issuer: str
    name: str
    metal_level: str
    plan_type: str
    year: int
    state: str
    county_fips: str | None
    deductible_ind: Decimal
    oop_max_ind: Decimal
    hsa_eligible: bool
    sbc_url: str | None
    service_area_id: str
    source_url: str
    retrieved_on: dt.date
    benefits: dict[tuple[str, str], CostShare] = field(default_factory=dict)


def puf_url(year: int, name: str) -> str:
    """Download URL for one PUF of one plan year."""
    return f"{PUF_BASE_URL}/{year}/{name}.zip"


def _decimal_money(text: str) -> Decimal | None:
    """Parse ``$9,800`` or ``$0.00`` into a Decimal."""
    match = _MONEY.search(text or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def parse_cost_share(copay_text: str, coins_text: str) -> CostShare | None:
    """Turn a PUF copay/coinsurance pair into structured cost sharing.

    Written against the complete set of distinct strings in the Texas slice of
    the 2026 benefits PUF (167 copay forms, 38 coinsurance forms), namely:

    * ``''`` and ``Not Applicable`` -- this tier is not priced
    * ``$0.00``, ``$40.00``, ``$1,000.00`` -- a flat copay, deductible not applied
    * ``$80.00 Copay after deductible`` / ``$2000.00 Copay with deductible``
    * ``$950.00 Copay per Stay`` (+ optional ``with deductible``)
    * ``No Charge`` and ``No Charge after deductible``
    * ``50.00%``, ``100.00%``, ``0.00%``
    * ``50.00% Coinsurance after deductible``

    Returns:
        ``CostShare``, or None when neither field prices this tier.
    """
    copay_raw = (copay_text or "").strip()
    coins_raw = (coins_text or "").strip()
    copay_na = copay_raw.lower() in _NOT_APPLICABLE
    coins_na = coins_raw.lower() in _NOT_APPLICABLE
    if copay_na and coins_na:
        return None

    after_deductible = bool(
        _DEDUCTIBLE_APPLIES.search(copay_raw) or _DEDUCTIBLE_APPLIES.search(coins_raw)
    )

    copay: Decimal | None = None
    if not copay_na:
        if copay_raw.lower().startswith("no charge"):
            copay = Decimal("0")
        else:
            copay = _decimal_money(copay_raw)

    coinsurance: Decimal | None = None
    if not coins_na:
        if coins_raw.lower().startswith("no charge"):
            coinsurance = Decimal("0")
        else:
            percent = _PERCENT.search(coins_raw)
            if percent:
                coinsurance = (Decimal(percent.group(1)) / Decimal("100")).quantize(
                    Decimal("0.0001")
                )

    if copay is None and coinsurance is None:
        return None

    qualifier = None
    stay = re.search(r"per\s+([A-Za-z ]+?)(?:\s+with deductible|\s+after deductible|$)", copay_raw)
    if stay:
        qualifier = f"per {stay.group(1).strip()}"

    display = " / ".join(
        part for part in (copay_raw, coins_raw) if part and part.lower() not in _NOT_APPLICABLE
    )
    return CostShare(
        copay=copay,
        coinsurance=coinsurance,
        after_deductible=after_deductible,
        display_text=display,
        qualifier=qualifier,
    )


def _stream_puf_rows(year: int, name: str) -> Iterator[dict[str, str]]:
    """Yield rows of one PUF as dicts, streaming from inside the zip.

    The benefits PUF is 375 MB uncompressed, so it is read through the zip
    member's own stream rather than extracted or loaded into memory.
    """
    url = puf_url(year, name)
    archive = fetch_bytes(url)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        member = max(zf.infolist(), key=lambda info: info.file_size).filename
        logger.info("streaming %s from %s", member, url)
        with zf.open(member) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace")
            yield from csv.DictReader(text)


def service_area_ids_for_county(
    year: int, state: str, county_fips: str
) -> dict[tuple[str, str], str]:
    """Service areas that cover one county, keyed by ``(issuer_id, area_id)``.

    Statewide areas (``CoverEntireState == 'Yes'``) count as covering every
    county in the state. The value is the matching service area's name, which
    makes the selection auditable.
    """
    areas: dict[tuple[str, str], str] = {}
    for row in _stream_puf_rows(year, "service-area-puf"):
        if row.get("StateCode") != state.upper():
            continue
        if row.get("DentalOnlyPlan") == "Yes":
            continue
        if row.get("MarketCoverage") != INDIVIDUAL_MARKET:
            continue
        statewide = row.get("CoverEntireState") == "Yes"
        county = (row.get("County") or "").strip().split(".")[0]
        if statewide or county == county_fips:
            key = (row.get("IssuerId", ""), row.get("ServiceAreaId", ""))
            areas[key] = row.get("ServiceAreaName", "")
    logger.info(
        "%d individual-market service areas cover %s county %s in %d",
        len(areas),
        state,
        county_fips,
        year,
    )
    return areas


def _variant_rank(plan_id: str) -> int:
    """Sort key preferring the on-exchange standard variant of a plan."""
    for rank, suffix in enumerate(STANDARD_VARIANT_SUFFIXES):
        if plan_id.endswith(suffix):
            return rank
    return len(STANDARD_VARIANT_SUFFIXES)


def load_plans(
    year: int,
    state: str,
    county_fips: str,
    *,
    retrieved_on: dt.date | None = None,
) -> list[PlanRecord]:
    """Read every individual medical plan sold in one county.

    Cost sharing is not loaded here; call :func:`attach_benefits` with the
    plans you actually keep, so the 1.46-million-row benefits file is streamed
    once for a small set of plan IDs.
    """
    retrieved = retrieved_on or dt.date.today()
    areas = service_area_ids_for_county(year, state, county_fips)
    if not areas:
        raise ValueError(
            f"no individual-market service areas cover {state} county {county_fips} in {year}"
        )

    source_url = puf_url(year, "plan-attributes-puf")
    best: dict[str, PlanRecord] = {}
    best_variant: dict[str, str] = {}

    for row in _stream_puf_rows(year, "plan-attributes-puf"):
        if row.get("StateCode") != state.upper():
            continue
        if row.get("DentalOnlyPlan") == "Yes":
            continue
        if row.get("MarketCoverage") != INDIVIDUAL_MARKET:
            continue
        if (row.get("IssuerId", ""), row.get("ServiceAreaId", "")) not in areas:
            continue
        if not str(row.get("CSRVariationType", "")).startswith("Standard"):
            continue

        plan_id = (row.get("PlanId") or "").strip()
        standard_id = (row.get("StandardComponentId") or "").strip()
        if not plan_id or not standard_id:
            continue

        deductible, oop_max = _accumulator_limits(row)
        if deductible is None or oop_max is None:
            continue  # cannot price a plan whose limits the filing omits

        record = PlanRecord(
            plan_id=standard_id,
            hios_id=standard_id,
            issuer=(row.get("IssuerMarketPlaceMarketingName") or "").strip(),
            name=(row.get("PlanMarketingName") or "").strip(),
            metal_level=(row.get("MetalLevel") or "").strip(),
            plan_type=(row.get("PlanType") or "").strip(),
            year=year,
            state=state.upper(),
            county_fips=county_fips,
            deductible_ind=deductible,
            oop_max_ind=oop_max,
            hsa_eligible=row.get("IsHSAEligible") == "Yes",
            sbc_url=(row.get("URLForSummaryofBenefitsCoverage") or "").strip() or None,
            service_area_id=(row.get("ServiceAreaId") or "").strip(),
            source_url=source_url,
            retrieved_on=retrieved,
        )
        previous = best_variant.get(standard_id)
        if previous is None or _variant_rank(plan_id) < _variant_rank(previous):
            best[standard_id] = record
            best_variant[standard_id] = plan_id

    plans = sorted(best.values(), key=lambda p: (p.metal_level, p.issuer, p.plan_id))
    logger.info("%d distinct plans available in %s county %s", len(plans), state, county_fips)
    return plans


def _accumulator_limits(row: dict[str, str]) -> tuple[Decimal | None, Decimal | None]:
    """Individual in-network deductible and out-of-pocket maximum for a plan.

    Prefers the total (medical + drug) figures when the filing says the two
    accumulators are integrated, because that is the single number a member
    sees on their SBC.
    """
    integrated = row.get("MedicalDrugDeductiblesIntegrated") == "Yes"
    deductible_keys = (
        ["TEHBDedInnTier1Individual", "MEHBDedInnTier1Individual"]
        if integrated
        else ["MEHBDedInnTier1Individual", "TEHBDedInnTier1Individual"]
    )
    moop_integrated = row.get("MedicalDrugMaximumOutofPocketIntegrated") == "Yes"
    moop_keys = (
        ["TEHBInnTier1IndividualMOOP", "MEHBInnTier1IndividualMOOP"]
        if moop_integrated
        else ["MEHBInnTier1IndividualMOOP", "TEHBInnTier1IndividualMOOP"]
    )

    deductible = next(
        (
            value
            for value in (_decimal_money(row.get(key, "")) for key in deductible_keys)
            if value is not None
        ),
        None,
    )
    oop_max = next(
        (
            value
            for value in (_decimal_money(row.get(key, "")) for key in moop_keys)
            if value is not None
        ),
        None,
    )
    return deductible, oop_max


def attach_benefits(year: int, plans: list[PlanRecord]) -> int:
    """Stream the benefits PUF once and fill in cost sharing for ``plans``.

    Returns:
        How many ``(plan, benefit, tier)`` cost-sharing rows were attached.
    """
    by_standard_id = {plan.plan_id: plan for plan in plans}
    if not by_standard_id:
        return 0

    # Benefit rows exist per CSR variant; keep the lowest-numbered variant seen
    # for each (plan, benefit) so the standard plan design wins.
    chosen_variant: dict[tuple[str, str], str] = {}
    attached = 0

    for row in _stream_puf_rows(year, "benefits-and-cost-sharing-puf"):
        standard_id = row.get("StandardComponentId") or ""
        plan = by_standard_id.get(standard_id)
        if plan is None:
            continue
        benefit = (row.get("BenefitName") or "").strip()
        if not benefit:
            continue
        variant = row.get("PlanId") or ""
        key = (standard_id, benefit)
        current = chosen_variant.get(key)
        if current is not None and _variant_rank(variant) >= _variant_rank(current):
            continue
        chosen_variant[key] = variant

        covered = (row.get("IsCovered") or "").strip().lower() == "covered"
        in_network = parse_cost_share(row.get("CopayInnTier1", ""), row.get("CoinsInnTier1", ""))
        out_network = parse_cost_share(row.get("CopayOutofNet", ""), row.get("CoinsOutofNet", ""))

        for tier, parsed in (("in_network", in_network), ("out_of_network", out_network)):
            if parsed is None and covered:
                continue  # covered, but this tier carries no priced cost sharing
            share = parsed or CostShare(
                copay=None,
                coinsurance=None,
                after_deductible=False,
                display_text="Not covered by this plan",
                is_covered=False,
            )
            if not covered:
                share = CostShare(
                    copay=share.copay,
                    coinsurance=share.coinsurance,
                    after_deductible=share.after_deductible,
                    display_text=share.display_text,
                    qualifier=share.qualifier,
                    is_covered=False,
                )
            plan.benefits[(benefit, tier)] = share
            attached += 1

    logger.info("attached %d cost-sharing rows across %d plans", attached, len(plans))
    return attached
