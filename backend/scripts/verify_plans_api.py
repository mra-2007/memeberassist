"""Cross-check our stored plans against the live CMS Marketplace API.

    uv run python manage.py verify-plans            # report differences only
    uv run python manage.py verify-plans --apply    # and correct what differs

Why this exists. Phase 1 loaded plans from the CMS Marketplace **Public Use
Files**, because no API key was available. The PUFs are the issuers' own QHP
filings, so they should agree with the API — but "should agree" is exactly the
kind of claim this project is not allowed to assert without checking. This
script checks it, records the HIOS plan ids and any differences in
``docs/data_sources.md``, and corrects only the fields that actually differ.

**The PUF path stays the fallback.** Nothing here replaces ``fetch_plans``; it
reconciles what ``fetch_plans`` produced. If the API is unreachable or the key
has expired, the stored data is still the real filings and the demo still works.

The key expires every 60 days, so a 401 is reported as an expired or missing
credential with the renewal URL, not as a code failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Plan
from app.db.session import session_scope
from app.external.cache import default_headers, save_sample
from scripts._common import console, money, setup_logging

logger = logging.getLogger(__name__)

PLAN_SEARCH_URL = "https://marketplace.api.healthcare.gov/api/v1/plans/search"
PLAN_DETAIL_URL = "https://marketplace.api.healthcare.gov/api/v1/plans/{plan_id}"
KEY_REQUEST_URL = "https://developer.cms.gov/marketplace-api"

#: A household is required by the search endpoint. These values do not affect a
#: plan's deductible or out-of-pocket maximum, which is what we compare; they
#: only decide which plans are returned and what subsidy is quoted.
REFERENCE_HOUSEHOLD = {
    "income": 52000,
    "people": [{"age": 65, "aptc_eligible": True, "gender": "Male", "uses_tobacco": False}],
}

#: Dallas County, matching PLAN_STATE / PLAN_COUNTY_FIPS in .env.
REFERENCE_ZIP = "75252"


class MissingApiKeyError(RuntimeError):
    """The Marketplace API key is absent or expired."""


@dataclass
class Difference:
    """One field where the API and the database disagree."""

    plan_id: str
    field_name: str
    stored: str
    live: str

    def as_row(self) -> tuple[str, str, str, str]:
        return (self.plan_id, self.field_name, self.stored, self.live)


@dataclass
class PlanComparison:
    """The result of comparing one plan."""

    plan_id: str
    hios_id: str
    found: bool
    api_name: str = ""
    differences: list[Difference] = field(default_factory=list)


def _client() -> httpx.Client:
    """An HTTP client with the polite headers the API asks for."""
    return httpx.Client(
        timeout=45.0,
        headers={**default_headers(), "Content-Type": "application/json"},
        follow_redirects=True,
    )


def _api_key() -> str:
    """The configured key.

    Raises:
        MissingApiKeyError: when it is absent.
    """
    key = get_settings().cms_marketplace_api_key.strip()
    if not key:
        raise MissingApiKeyError(
            "CMS_MARKETPLACE_API_KEY is empty in .env. The live cross-check cannot run. "
            f"Request or renew a key at {KEY_REQUEST_URL} — it expires every 60 days."
        )
    return key


def search_plans(*, limit: int = 100, save: bool = True) -> list[dict]:
    """Fetch the plans offered in the configured county from the live API.

    Args:
        limit: Plans to request.
        save: Write a sample response to ``data/samples`` for reference.

    Returns:
        The API's plan objects.

    Raises:
        MissingApiKeyError: when no key is configured.
        RuntimeError: when the API answers with an error.
    """
    settings = get_settings()
    key = _api_key()
    body = {
        "household": REFERENCE_HOUSEHOLD,
        "market": "Individual",
        "place": {
            "countyfips": settings.plan_county_fips,
            "state": settings.plan_state,
            "zipcode": REFERENCE_ZIP,
        },
        "year": settings.plan_year,
        "limit": limit,
    }
    with _client() as client:
        response = client.post(PLAN_SEARCH_URL, params={"apikey": key}, json=body)

    if response.status_code in (401, 403):
        raise MissingApiKeyError(
            f"The API rejected the key with HTTP {response.status_code}: "
            f"{response.text[:200]}. Keys expire every 60 days — renew at {KEY_REQUEST_URL}."
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"{PLAN_SEARCH_URL} returned HTTP {response.status_code}: {response.text[:300]}"
        )

    payload = response.json()
    if save:
        save_sample("cms_marketplace_plans_search", payload)
    plans = payload.get("plans") or []
    logger.info(
        "API returned %d plans for %s %s", len(plans), settings.plan_state, settings.plan_year
    )
    return list(plans)


def _decimal(value: object) -> Decimal | None:
    """Read a money value out of the API payload."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (ArithmeticError, ValueError):
        return None


def _individual_deductible(api_plan: dict) -> Decimal | None:
    """The individual medical deductible from an API plan object.

    The API reports deductibles as a list of named amounts, because a plan can
    carry separate medical and drug deductibles. The one comparable with our
    ``plans.deductible_ind`` is the individual medical one.
    """
    for entry in api_plan.get("deductibles") or []:
        if entry.get("family_cost") == "Family":
            continue
        name = str(entry.get("type") or entry.get("name") or "").lower()
        if "drug" in name:
            continue
        amount = _decimal(entry.get("amount"))
        if amount is not None:
            return amount
    return None


def _individual_oop_max(api_plan: dict) -> Decimal | None:
    """The individual medical out-of-pocket maximum from an API plan object."""
    for entry in api_plan.get("moops") or []:
        if entry.get("family_cost") == "Family":
            continue
        name = str(entry.get("type") or entry.get("name") or "").lower()
        if "drug" in name:
            continue
        amount = _decimal(entry.get("amount"))
        if amount is not None:
            return amount
    return None


def compare(session: Session, api_plans: list[dict]) -> list[PlanComparison]:
    """Compare every stored plan with its live counterpart."""
    by_id = {str(plan.get("id")): plan for plan in api_plans}
    results: list[PlanComparison] = []

    for plan in session.scalars(select(Plan).order_by(Plan.plan_id)):
        api_plan = by_id.get(plan.plan_id)
        if api_plan is None:
            results.append(PlanComparison(plan_id=plan.plan_id, hios_id=plan.hios_id, found=False))
            continue

        comparison = PlanComparison(
            plan_id=plan.plan_id,
            hios_id=plan.hios_id,
            found=True,
            api_name=str(api_plan.get("name") or ""),
        )

        checks: list[tuple[str, object, object]] = [
            ("name", plan.name, api_plan.get("name")),
            ("issuer", plan.issuer, (api_plan.get("issuer") or {}).get("name")),
            ("metal_level", plan.metal_level, api_plan.get("metal_level")),
            ("plan_type", plan.plan_type, api_plan.get("type")),
            ("deductible_ind", plan.deductible_ind, _individual_deductible(api_plan)),
            ("oop_max_ind", plan.oop_max_ind, _individual_oop_max(api_plan)),
            ("hsa_eligible", plan.hsa_eligible, api_plan.get("hsa_eligible")),
        ]
        for field_name, stored, live in checks:
            if live is None:
                continue
            if isinstance(stored, Decimal):
                live_value = _decimal(live)
                if live_value is None or stored == live_value:
                    continue
                comparison.differences.append(
                    Difference(plan.plan_id, field_name, money(stored), money(live_value))
                )
                continue
            if isinstance(stored, bool):
                if bool(live) != stored:
                    comparison.differences.append(
                        Difference(plan.plan_id, field_name, str(stored), str(bool(live)))
                    )
                continue
            if str(stored).strip().lower() != str(live).strip().lower():
                comparison.differences.append(
                    Difference(plan.plan_id, field_name, str(stored), str(live))
                )
        results.append(comparison)
    return results


def apply_corrections(
    session: Session, api_plans: list[dict], comparisons: list[PlanComparison]
) -> int:
    """Correct only the fields that differ, leaving everything else alone.

    Returns:
        How many fields were changed.
    """
    by_id = {str(plan.get("id")): plan for plan in api_plans}
    changed = 0
    for comparison in comparisons:
        if not comparison.differences:
            continue
        plan = session.get(Plan, comparison.plan_id)
        api_plan = by_id.get(comparison.plan_id)
        if plan is None or api_plan is None:
            continue
        for difference in comparison.differences:
            if difference.field_name == "deductible_ind":
                value = _individual_deductible(api_plan)
                if value is not None:
                    plan.deductible_ind = value
                    changed += 1
            elif difference.field_name == "oop_max_ind":
                value = _individual_oop_max(api_plan)
                if value is not None:
                    plan.oop_max_ind = value
                    changed += 1
            elif difference.field_name == "name":
                plan.name = str(api_plan.get("name"))
                changed += 1
            elif difference.field_name == "metal_level":
                plan.metal_level = str(api_plan.get("metal_level"))
                changed += 1
            elif difference.field_name == "hsa_eligible":
                plan.hsa_eligible = bool(api_plan.get("hsa_eligible"))
                changed += 1
            else:
                console.print(
                    f"  [yellow]{difference.plan_id}.{difference.field_name} differs but is not "
                    f"auto-corrected[/yellow] — change it deliberately, with a note."
                )
    return changed


def _report(comparisons: list[PlanComparison]) -> None:
    """Print the HIOS ids and every difference found."""
    table = Table(title="Stored plans vs the live CMS Marketplace API")
    for column in ("Plan id", "HIOS id", "In API", "Differences"):
        table.add_column(column)
    for comparison in comparisons:
        table.add_row(
            comparison.plan_id,
            comparison.hios_id,
            "[green]yes[/green]" if comparison.found else "[red]no[/red]",
            str(len(comparison.differences)) if comparison.found else "-",
        )
    console.print(table)

    differences = [d for c in comparisons for d in c.differences]
    if not differences:
        console.print(
            "\n[green]No differences.[/green] The Public Use Files and the live API agree on "
            "every compared field, which is what the provenance claim in docs/data_sources.md "
            "predicted but had not been checked."
        )
        return

    detail = Table(title=f"{len(differences)} difference(s)")
    for column in ("Plan", "Field", "Stored (PUF)", "Live (API)"):
        detail.add_column(column)
    for difference in differences:
        detail.add_row(*difference.as_row())
    console.print(detail)


def main(argv: list[str] | None = None) -> int:
    """Cross-check the stored plans against the live API."""
    parser = argparse.ArgumentParser(
        description="Cross-check plans against the CMS Marketplace API."
    )
    parser.add_argument("--apply", action="store_true", help="Correct the fields that differ.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        api_plans = search_plans()
    except MissingApiKeyError as error:
        console.print(f"[red]Blocked:[/red] {error}")
        console.print(
            "[dim]The stored plans remain the real CMS filings from the Public Use Files, so "
            "nothing is broken by this — the cross-check simply cannot run.[/dim]"
        )
        return 2
    except (RuntimeError, httpx.HTTPError) as error:
        console.print(f"[red]The Marketplace API could not be reached:[/red] {error}")
        return 1

    console.print(
        f"Live API returned [bold]{len(api_plans)}[/bold] plans for the configured county."
    )

    with session_scope() as session:
        comparisons = compare(session, api_plans)
        _report(comparisons)

        if args.apply:
            changed = apply_corrections(session, api_plans, comparisons)
            console.print(
                f"\n[bold]{changed}[/bold] field(s) corrected from the live API "
                f"on {dt.date.today().isoformat()}."
            )
            if changed:
                console.print(
                    "[yellow]Re-run `manage.py test` and, if a deductible or out-of-pocket "
                    "maximum changed, `manage.py make-data` so the accumulators and claims are "
                    "settled against the corrected terms.[/yellow]"
                )
        elif any(c.differences for c in comparisons):
            console.print("\n[dim]Nothing was changed. Re-run with --apply to correct these.[/dim]")

    missing = [c for c in comparisons if not c.found]
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
