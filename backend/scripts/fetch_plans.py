"""Load real Marketplace plans and their cost sharing into the database.

    uv run python manage.py fetch-plans

Source: the CMS Exchange Public Use Files for ``PLAN_YEAR``, filtered to
individual medical plans sold in ``PLAN_STATE`` / ``PLAN_COUNTY_FIPS``. See
``app/external/cms_puf.py`` for why the PUFs are used rather than the
Marketplace API, and ``docs/data_sources.md`` for licence notes.

Plan selection is deliberate and printed, not random. MemberAssist needs a
handful of genuinely different designs so that coverage, cost and
prior-authorization answers differ between members:

* one Bronze, preferring an HSA-eligible one;
* one Silver;
* one Gold, preferring a PPO;
* one HMO or EPO not already chosen.

At least one selected plan must price advanced imaging as **coinsurance after
deductible**, because the demo scenario and the cost-estimator tests depend on
that design existing. The script fails loudly rather than quietly shipping a
plan set the demo cannot run on.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import DATA_DIR, get_settings
from app.db.models import Plan, PlanBenefit
from app.db.session import session_scope
from app.external.cms_puf import (
    PUF_LANDING_PAGE,
    CostShare,
    PlanRecord,
    attach_benefits,
    load_plans,
    puf_url,
)
from scripts._common import console, money, percent, print_row_counts, record_source, setup_logging

#: The benefit category advanced imaging falls under, used by the design check.
IMAGING_BENEFIT = "Imaging (CT/PET Scans, MRIs)"

#: Benefit categories MemberAssist actually answers about. Everything else in
#: the filing is still loaded, but these must be present on every chosen plan.
REQUIRED_BENEFITS = (
    "Primary Care Visit to Treat an Injury or Illness",
    "Specialist Visit",
    "Imaging (CT/PET Scans, MRIs)",
    "X-rays and Diagnostic Imaging",
    "Laboratory Outpatient and Professional Services",
    "Emergency Room Services",
    "Urgent Care Centers or Facilities",
    "Outpatient Rehabilitation Services",
    "Generic Drugs",
    "Preventive Care/Screening/Immunization",
)

LICENCE_NOTE = (
    "US federal government work, public domain. The Marketplace PUFs are the "
    "issuers' own QHP filings as published by CMS; no redistribution "
    "restriction. Plan marketing names and issuer names are the issuers' "
    "trademarks and are reproduced factually."
)


def _is_coinsurance_after_deductible(share: CostShare | None) -> bool:
    """True when a benefit is priced as coinsurance that follows the deductible."""
    return bool(
        share
        and share.is_covered
        and share.after_deductible
        and share.coinsurance is not None
        and share.coinsurance > 0
    )


#: Plan types that cover out-of-network care at all, best first. Dallas County
#: has no PPO on the 2026 exchange, so POS is the realistic stand-in.
OUT_OF_NETWORK_TYPES = ("PPO", "POS")
#: Plan types that generally do not cover out-of-network care.
NETWORK_RESTRICTED_TYPES = ("HMO", "EPO")


def _prefers(plan: PlanRecord, types: tuple[str, ...]) -> int:
    """Rank a plan by how well its type matches a preference list."""
    upper = plan.plan_type.upper()
    return types.index(upper) if upper in types else len(types)


def select_plan_set(plans: list[PlanRecord]) -> list[tuple[PlanRecord, str]]:
    """Choose a small, varied, deterministic plan set, with the reason for each.

    Cost sharing must already be attached: the selection needs to see how each
    plan prices advanced imaging, because MemberAssist's demo and its cost
    tests depend on at least one coinsurance-after-deductible design.
    """
    chosen: list[tuple[PlanRecord, str]] = []
    taken: set[str] = set()

    def pick(reason: str, *, predicate, rank=None) -> None:
        candidates = [p for p in plans if p.plan_id not in taken and predicate(p)]
        if not candidates:
            console.print(f"  [yellow]![/yellow] nothing matched: {reason}")
            return
        # Prefer a coinsurance-after-deductible imaging design, then the
        # caller's own ranking, then the lowest deductible, then the plan id.
        best = min(
            candidates,
            key=lambda p: (
                0
                if _is_coinsurance_after_deductible(p.benefits.get((IMAGING_BENEFIT, "in_network")))
                else 1,
                rank(p) if rank else 0,
                p.deductible_ind,
                p.plan_id,
            ),
        )
        chosen.append((best, reason))
        taken.add(best.plan_id)

    pick(
        "Bronze design, HSA-eligible where available",
        predicate=lambda p: "bronze" in p.metal_level.lower(),
        rank=lambda p: 0 if p.hsa_eligible else 1,
    )
    pick(
        "Silver design, the most commonly enrolled metal level",
        predicate=lambda p: p.metal_level.lower() == "silver",
    )
    pick(
        "Gold design with out-of-network coverage (PPO, else POS)",
        predicate=lambda p: p.metal_level.lower() == "gold",
        rank=lambda p: _prefers(p, OUT_OF_NETWORK_TYPES),
    )
    pick(
        "A network-restricted design (HMO or EPO), so out-of-network answers differ",
        predicate=lambda p: p.plan_type.upper() in NETWORK_RESTRICTED_TYPES,
    )
    return chosen


def _benefit_rows(plan: PlanRecord) -> list[PlanBenefit]:
    """Turn a plan's parsed cost sharing into ``plan_benefits`` rows."""
    rows: list[PlanBenefit] = []
    source = "cms_marketplace_puf_benefits_and_cost_sharing"
    for (benefit_name, tier), share in sorted(plan.benefits.items()):
        rows.append(
            PlanBenefit(
                plan_id=plan.plan_id,
                benefit_name=benefit_name,
                network_tier=tier,
                copay=share.copay,
                coinsurance=share.coinsurance,
                after_deductible=share.after_deductible,
                is_covered=share.is_covered,
                # Confirmed per plan from the plan's own documents in Phase 2;
                # the code mapping supplies the commercial-policy default.
                pa_required=False,
                display_text=share.display_text or "Not stated in the filing",
                source=source,
            )
        )
    return rows


def _write_plan_report(plans: list[tuple[PlanRecord, str]]) -> None:
    """Write the chosen plan set to ``data/mappings`` for review and for Phase 2."""
    target = DATA_DIR / "mappings" / "selected_plans.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "plan_id",
                "issuer",
                "name",
                "metal_level",
                "plan_type",
                "deductible_ind",
                "oop_max_ind",
                "hsa_eligible",
                "selection_reason",
                "sbc_url",
            ]
        )
        for plan, reason in plans:
            writer.writerow(
                [
                    plan.plan_id,
                    plan.issuer,
                    plan.name,
                    plan.metal_level,
                    plan.plan_type,
                    plan.deductible_ind,
                    plan.oop_max_ind,
                    "yes" if plan.hsa_eligible else "no",
                    reason,
                    plan.sbc_url or "",
                ]
            )
    console.print(f"[dim]wrote {target.relative_to(DATA_DIR.parent)}[/dim]")


def load(session: Session, *, year: int, state: str, county: str) -> list[PlanRecord]:
    """Fetch, select and store the plan set. Returns the stored plans."""
    console.print(
        f"[bold]Reading Marketplace PUFs for {state} county {county}, plan year {year}[/bold]"
    )
    available = load_plans(year, state, county)
    console.print(f"  {len(available)} individual medical plans available in that county")

    # One streaming pass over the 1.46-million-row benefits file covers every
    # candidate, so the selection can see each plan's real cost-sharing design.
    console.print("  reading cost sharing (one streaming pass over the benefits PUF)…")
    attached = attach_benefits(year, available)
    console.print(f"  read {attached:,} cost-sharing rows across {len(available)} plans")

    selected = select_plan_set(available)
    if not selected:
        raise SystemExit("No plans could be selected; check PLAN_STATE and PLAN_COUNTY_FIPS.")
    records = [plan for plan, _ in selected]

    _verify(selected)

    session.execute(delete(PlanBenefit))
    session.execute(delete(Plan))
    for plan, _reason in selected:
        session.add(
            Plan(
                plan_id=plan.plan_id,
                hios_id=plan.hios_id,
                issuer=plan.issuer,
                name=plan.name,
                metal_level=plan.metal_level,
                plan_type=plan.plan_type,
                year=plan.year,
                state=plan.state,
                county_fips=plan.county_fips,
                deductible_ind=plan.deductible_ind,
                oop_max_ind=plan.oop_max_ind,
                hsa_eligible=plan.hsa_eligible,
                source="cms_marketplace_puf_plan_attributes",
                source_url=plan.source_url,
                retrieved_on=plan.retrieved_on,
            )
        )
        session.add_all(_benefit_rows(plan))
    session.flush()

    benefit_rows = sum(len(plan.benefits) for plan, _ in selected)
    record_source(
        session,
        key="cms_marketplace_puf",
        name=f"CMS Health Insurance Marketplace Public Use Files, plan year {year}",
        url=PUF_LANDING_PAGE,
        licence_note=LICENCE_NOTE,
        row_count=benefit_rows,
        notes=(
            "Files used: "
            f"{puf_url(year, 'service-area-puf')}, "
            f"{puf_url(year, 'plan-attributes-puf')}, "
            f"{puf_url(year, 'benefits-and-cost-sharing-puf')}. "
            "Used because the Marketplace API returns 401 without a key "
            "(CMS_MARKETPLACE_API_KEY was not issued yet)."
        ),
        retrieved_on=records[0].retrieved_on,
    )
    _print_summary(selected)
    _write_plan_report(selected)
    return records


def _verify(selected: list[tuple[PlanRecord, str]]) -> None:
    """Fail loudly if the chosen plans cannot support the product's answers."""
    problems: list[str] = []
    for plan, _ in selected:
        missing = [
            benefit for benefit in REQUIRED_BENEFITS if (benefit, "in_network") not in plan.benefits
        ]
        if missing:
            problems.append(
                f"{plan.plan_id} has no in-network cost sharing for: {', '.join(missing)}"
            )
        if plan.deductible_ind < 0 or plan.oop_max_ind <= 0:
            problems.append(f"{plan.plan_id} has implausible limits")
        if plan.oop_max_ind < plan.deductible_ind:
            problems.append(
                f"{plan.plan_id}: out-of-pocket maximum {plan.oop_max_ind} is below "
                f"the deductible {plan.deductible_ind}"
            )

    coinsurance_plans = [
        plan.plan_id
        for plan, _ in selected
        if _is_coinsurance_after_deductible(plan.benefits.get((IMAGING_BENEFIT, "in_network")))
    ]
    if not coinsurance_plans:
        problems.append(
            "no selected plan prices advanced imaging as coinsurance after the "
            "deductible; the demo scenario and the cost-estimator tests need one"
        )

    if problems:
        for problem in problems:
            console.print(f"  [red]x[/red] {problem}")
        raise SystemExit("Plan set rejected. Fix the selection before seeding.")
    console.print(
        f"  [green]ok[/green] imaging is coinsurance-after-deductible on: {', '.join(coinsurance_plans)}"
    )


def _print_summary(selected: list[tuple[PlanRecord, str]]) -> None:
    """Print the plan set and its imaging cost sharing."""
    from rich.table import Table

    table = Table(title="Selected plans (real CMS filings)")
    for column in (
        "Plan ID",
        "Issuer",
        "Metal",
        "Type",
        "Deductible",
        "OOP max",
        "HSA",
        "Imaging (in-network)",
    ):
        table.add_column(column, overflow="fold")
    for plan, _reason in selected:
        share = plan.benefits.get((IMAGING_BENEFIT, "in_network"))
        imaging = "not stated"
        if share:
            parts = []
            if share.copay is not None:
                parts.append(f"{money(share.copay)} copay")
            if share.coinsurance is not None:
                parts.append(f"{percent(share.coinsurance)} coinsurance")
            if share.after_deductible:
                parts.append("after deductible")
            imaging = " ".join(parts) if parts else share.display_text
        table.add_row(
            plan.plan_id,
            plan.issuer[:28],
            plan.metal_level,
            plan.plan_type,
            money(plan.deductible_ind),
            money(plan.oop_max_ind),
            "yes" if plan.hsa_eligible else "no",
            imaging,
        )
    console.print(table)
    for plan, reason in selected:
        console.print(f"  [dim]{plan.plan_id}: {reason}[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=None, help="Plan year (default: PLAN_YEAR).")
    parser.add_argument("--state", default=None, help="Two-letter state (default: PLAN_STATE).")
    parser.add_argument("--county", default=None, help="County FIPS (default: PLAN_COUNTY_FIPS).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    settings = get_settings()
    year = args.year or settings.plan_year
    state = (args.state or settings.plan_state).upper()
    county = args.county or settings.plan_county_fips

    started = dt.datetime.now()
    with session_scope() as session:
        load(session, year=year, state=state, county=county)
        print_row_counts(session, "After fetch-plans")
    console.print(f"[green]Done in {(dt.datetime.now() - started).total_seconds():.0f}s[/green]")


if __name__ == "__main__":
    main()
