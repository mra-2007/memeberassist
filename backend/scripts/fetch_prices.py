"""Load Medicare benchmark prices for the procedure codes we answer about.

    uv run python manage.py fetch-prices

Source: the CMS Physician Fee Schedule relative value files. See
``app/external/cms_prices.py`` for the formula, the worked check and why the
specified ``data.cms.gov`` utilization dataset is not the source here.

The script re-probes that blocked dataset on every run and prints the result,
so the blocker is re-tested rather than assumed. If it ever becomes reachable,
the message says so and the submitted-charge column can be filled in.

Codes come from ``data/mappings/code_to_benefit.csv``. Any code the fee
schedule does not price is reported with the reason, as the specification
requires, rather than silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt

from rich.table import Table
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import DATA_DIR, get_settings
from app.db.models import ProcedurePrice
from app.db.session import session_scope
from app.external.cms_prices import (
    CLFS_LANDING_PAGE,
    CMS_UTILIZATION_DATASET_TITLE,
    PriceSourceError,
    fetch_benchmark_prices,
    fetch_lab_prices,
    utilization_dataset_status,
)
from scripts._common import console, money, print_row_counts, record_source, setup_logging

CODE_MAPPING_PATH = DATA_DIR / "mappings" / "code_to_benefit.csv"

LICENCE_NOTE = (
    "US federal government work, public domain, EXCEPT the HCPCS/CPT short "
    "descriptors: 'CPT codes and descriptions only are copyright 2026 American "
    "Medical Association. All Rights Reserved.' CMS publishes them in the "
    "public fee-schedule files; MemberAssist reproduces them factually to "
    "name a procedure and does not redistribute the CPT code set."
)


def load_wanted_codes() -> dict[str, str]:
    """Codes we price, mapped to their plain-language note from the mapping file."""
    if not CODE_MAPPING_PATH.exists():
        raise SystemExit(f"Missing {CODE_MAPPING_PATH}; the code mapping is required.")
    with CODE_MAPPING_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["hcpcs_code"].strip(): row.get("notes", "").strip()
        for row in rows
        if row.get("hcpcs_code")
    }


def load(session: Session, *, year: int) -> int:
    """Fetch and store benchmark prices. Returns the number of rows stored."""
    wanted = load_wanted_codes()
    console.print(f"[bold]Pricing {len(wanted)} procedure codes for {year}[/bold]")

    status = utilization_dataset_status()
    if status.get("reachable"):
        console.print(
            f"  [yellow]note[/yellow] {CMS_UTILIZATION_DATASET_TITLE} is now reachable "
            f"({status.get('matches')} matching datasets). Average submitted charges "
            "could be added as the high end of the estimate range."
        )
    else:
        console.print(
            f"  [dim]data.cms.gov still unreachable (HTTP {status.get('status')}); "
            "using the CMS Physician Fee Schedule instead[/dim]"
        )

    try:
        rows, pfs_url = fetch_benchmark_prices(set(wanted), year)
    except PriceSourceError as exc:
        raise SystemExit(f"Price source unavailable: {exc}") from exc
    console.print(f"  Physician Fee Schedule priced {len({r.hcpcs_code for r in rows})} codes")

    # Codes with PFS status 'X' are statutorily excluded from that schedule and
    # paid under the Clinical Laboratory Fee Schedule instead.
    remaining = set(wanted) - {row.hcpcs_code for row in rows}
    clfs_url = ""
    if remaining:
        try:
            lab_rows, clfs_url = fetch_lab_prices(remaining, year)
        except PriceSourceError as exc:
            console.print(
                f"  [yellow]![/yellow] Clinical Laboratory Fee Schedule unavailable: {exc}"
            )
            lab_rows = []
        if lab_rows:
            console.print(
                f"  Clinical Laboratory Fee Schedule priced "
                f"{len({r.hcpcs_code for r in lab_rows})} further codes"
            )
            rows = [*rows, *lab_rows]

    priced = {row.hcpcs_code for row in rows}
    missing = sorted(set(wanted) - priced)
    source_url = pfs_url

    session.execute(delete(ProcedurePrice))
    for row in rows:
        session.add(
            ProcedurePrice(
                hcpcs_code=row.hcpcs_code,
                description=row.description,
                place_of_service=row.place_of_service,
                avg_submitted_charge=row.avg_submitted_charge,
                avg_allowed=row.avg_allowed,
                avg_payment=row.avg_payment,
                data_year=row.data_year,
                source=row.source,
                source_url=row.source_url,
                derivation=row.derivation,
                retrieved_on=row.retrieved_on,
            )
        )
    session.flush()

    lab_rows = [row for row in rows if row.source == "cms_clfs"]
    if lab_rows and clfs_url:
        record_source(
            session,
            key="cms_clfs",
            name=f"CMS Clinical Laboratory Fee Schedule, {year}",
            url=CLFS_LANDING_PAGE,
            licence_note=(
                "US federal government work, public domain, EXCEPT the CPT short "
                "descriptors: 'CPT codes, descriptions and other data only are "
                "copyright 2026 American Medical Association. All rights reserved.'"
            ),
            row_count=len(lab_rows),
            notes=(
                f"File used: {clfs_url}. Prices the five laboratory codes that carry "
                "Physician Fee Schedule status 'X' (statutory exclusion). Medicare "
                "pays 100% of the rate: clinical diagnostic laboratory tests carry "
                "no Part B coinsurance or deductible."
            ),
            retrieved_on=lab_rows[0].retrieved_on,
        )

    conversion_factors = {row.conversion_factor for row in rows if row.source == "cms_pfs_rvu"}
    record_source(
        session,
        key="cms_pfs_rvu",
        name=f"CMS Physician Fee Schedule relative value files, {year}",
        url=source_url,
        licence_note=LICENCE_NOTE,
        row_count=len([r for r in rows if r.source == "cms_pfs_rvu"]),
        notes=(
            f"National allowed amount = total RVU x conversion factor "
            f"{sorted(conversion_factors)}; Medicare payment = 80% of allowed. "
            "avg_submitted_charge is NULL because the fee schedule does not "
            "publish submitted charges and data.cms.gov is unreachable "
            f"(HTTP {status.get('status')})."
        ),
        retrieved_on=rows[0].retrieved_on if rows else dt.date.today(),
    )

    _print_prices(rows)
    if missing:
        console.print(
            f"\n  [yellow]{len(missing)} code(s) not priced by the fee schedule:[/yellow]"
        )
        for code in missing:
            console.print(f"    {code}  {wanted[code]}  [dim](no payable global row)[/dim]")
        console.print(
            "  [dim]No CMS fee schedule publishes a national rate for these. They remain "
            "answerable for coverage and claim questions; only the cost estimate "
            "declines to quote a price.[/dim]"
        )
    return len(rows)


def _print_prices(rows: list) -> None:
    """Print the loaded prices."""
    table = Table(title="Medicare benchmark prices (CMS fee schedules)")
    for column in ("Code", "Description", "Setting", "Allowed", "Medicare pays", "Source"):
        table.add_column(column, overflow="fold")
    labels = {"cms_pfs_rvu": "PFS", "cms_clfs": "CLFS"}
    for row in sorted(rows, key=lambda r: (r.hcpcs_code, r.place_of_service)):
        table.add_row(
            row.hcpcs_code,
            row.description[:34],
            row.place_of_service,
            money(row.avg_allowed),
            money(row.avg_payment),
            labels.get(row.source, row.source),
        )
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=None, help="Fee schedule year.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    year = args.year or get_settings().plan_year
    with session_scope() as session:
        stored = load(session, year=year)
        print_row_counts(session, "After fetch-prices")
    console.print(f"[green]Stored {stored} price rows[/green]")


if __name__ == "__main__":
    main()
