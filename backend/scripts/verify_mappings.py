"""Verify every code in the mapping files against an authoritative source.

    uv run python manage.py verify-mappings

Project rule 7.6 requires that every ICD-10-CM code in the SNOMED mapping is
**verified via NLM Clinical Tables** rather than trusted. This script does
that, writes the official title back into the mapping file, and exits non-zero
if anything fails -- so a typo cannot reach a member's claim explanation.

Procedure codes are verified differently. Clinical Tables holds HCPCS Level II
only; five-digit CPT codes such as 73721 return nothing because CPT is
licensed by the AMA. Those are instead checked against ``procedure_prices``,
which was built from the CMS fee schedules, so the authority is CMS's own
published descriptor.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt

from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.db.models import PlanBenefit, ProcedurePrice
from app.db.session import session_scope
from app.external.clinical_tables import ICD10CM_URL, classify_code, lookup_hcpcs, lookup_icd10
from scripts._common import console, record_source, setup_logging

MAPPINGS = DATA_DIR / "mappings"
SNOMED_PATH = MAPPINGS / "snomed_to_icd10.csv"
CODE_BENEFIT_PATH = MAPPINGS / "code_to_benefit.csv"
SYNONYMS_PATH = MAPPINGS / "procedure_synonyms.csv"

LICENCE_NOTE = (
    "NLM Clinical Tables and the ICD-10-CM code set are free to use; ICD-10-CM "
    "is maintained by CDC/NCHS and is in the public domain in the United States."
)


def verify_icd10(write_back: bool = True) -> tuple[int, list[str]]:
    """Check every ICD-10-CM code in the SNOMED mapping.

    Returns:
        ``(verified_count, failures)`` where each failure names the code.
    """
    if not SNOMED_PATH.exists():
        raise SystemExit(f"Missing {SNOMED_PATH}")
    with SNOMED_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    console.print(f"[bold]Verifying {len(rows)} ICD-10-CM codes via NLM Clinical Tables[/bold]")
    failures: list[str] = []
    verified = 0
    today = dt.date.today().isoformat()

    for row in rows:
        code = (row.get("icd10_code") or "").strip()
        if not code:
            failures.append("(blank icd10_code)")
            continue
        if classify_code(code) != "icd10cm":
            failures.append(f"{code} (not an ICD-10-CM shaped code)")
            row["verified_title"] = ""
            row["verified_on"] = ""
            continue
        title = lookup_icd10(code)
        if title is None:
            failures.append(f"{code} ({row.get('snomed_display', '')[:44]})")
            row["verified_title"] = ""
            row["verified_on"] = ""
        else:
            verified += 1
            row["verified_title"] = title
            row["verified_on"] = today

    if write_back:
        fieldnames = [
            "snomed_code",
            "snomed_display",
            "icd10_code",
            "category",
            "claimable",
            "mapping_note",
            "verified_title",
            "verified_on",
        ]
        with SNOMED_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        console.print(f"  [dim]wrote verified titles back into {SNOMED_PATH.name}[/dim]")

    return verified, failures


def verify_procedure_codes(session: Session) -> tuple[int, list[str], int]:
    """Check procedure codes against CMS prices, and HCPCS Level II against NLM.

    Returns:
        ``(verified_count, failures, hcpcs_level2_checked)``.
    """
    with CODE_BENEFIT_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    priced = {
        code for code in session.scalars(select(ProcedurePrice.hcpcs_code).distinct()) if code
    }
    descriptions = {
        row.hcpcs_code: row.description for row in session.scalars(select(ProcedurePrice))
    }
    benefit_names = set(session.scalars(select(PlanBenefit.benefit_name).distinct()))

    console.print(f"\n[bold]Verifying {len(rows)} procedure codes and benefit mappings[/bold]")
    failures: list[str] = []
    verified = 0
    level2_checked = 0

    for row in rows:
        code = (row.get("hcpcs_code") or "").strip()
        benefit = (row.get("benefit_name") or "").strip()
        kind = classify_code(code)

        if kind == "hcpcs_level2":
            level2_checked += 1
            if lookup_hcpcs(code) is None:
                failures.append(f"{code}: not found in NLM Clinical Tables HCPCS")
                continue
        if code not in priced:
            failures.append(f"{code}: no CMS price row, so no authoritative descriptor")
        else:
            verified += 1

        # The benefit name must join to a real plan_benefits row, or the cost
        # estimator will silently find no plan rule for this procedure.
        if benefit not in benefit_names:
            failures.append(
                f"{code}: benefit_name {benefit!r} does not match any plan_benefits row"
            )

    _print_codes(rows, descriptions)
    return verified, failures, level2_checked


def _print_codes(rows: list[dict[str, str]], descriptions: dict[str, str]) -> None:
    """Print each procedure code with the descriptor CMS publishes for it."""
    table = Table(title="Procedure codes (descriptor from the CMS fee schedules)")
    for column in ("Code", "Type", "CMS descriptor", "Benefit category", "PA commonly required"):
        table.add_column(column, overflow="fold")
    for row in rows:
        code = row["hcpcs_code"].strip()
        table.add_row(
            code,
            classify_code(code),
            descriptions.get(code, "[red]no price row[/red]")[:32],
            row["benefit_name"][:34],
            row.get("pa_commonly_required", ""),
        )
    console.print(table)


def verify_synonyms() -> list[str]:
    """Every synonym must point at a code that exists in the code mapping."""
    with CODE_BENEFIT_PATH.open(newline="", encoding="utf-8") as handle:
        known = {row["hcpcs_code"].strip() for row in csv.DictReader(handle)}
    with SYNONYMS_PATH.open(newline="", encoding="utf-8") as handle:
        synonyms = list(csv.DictReader(handle))

    failures = [
        f"synonym {row['phrase']!r} -> {row['hcpcs_code']} is not in code_to_benefit.csv"
        for row in synonyms
        if row.get("hcpcs_code", "").strip() not in known
    ]
    console.print(
        f"\n[bold]Checked {len(synonyms)} plain-language synonyms[/bold] "
        f"covering {len({r['hcpcs_code'] for r in synonyms})} codes"
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    icd_verified, icd_failures = verify_icd10()
    console.print(
        f"  [green]{icd_verified} verified[/green]"
        + (f", [red]{len(icd_failures)} failed[/red]" if icd_failures else "")
    )
    for failure in icd_failures:
        console.print(f"    [red]x[/red] {failure}")

    with session_scope() as session:
        code_verified, code_failures, level2 = verify_procedure_codes(session)
        console.print(
            f"  [green]{code_verified} procedure codes have a CMS descriptor[/green]"
            f" ({level2} HCPCS Level II also checked against NLM)"
        )
        for failure in code_failures:
            console.print(f"    [red]x[/red] {failure}")

        synonym_failures = verify_synonyms()
        for failure in synonym_failures:
            console.print(f"    [red]x[/red] {failure}")

        total_failures = len(icd_failures) + len(code_failures) + len(synonym_failures)
        if total_failures == 0:
            record_source(
                session,
                key="nlm_clinical_tables",
                name="NLM Clinical Tables (ICD-10-CM and HCPCS search)",
                url=ICD10CM_URL,
                licence_note=LICENCE_NOTE,
                row_count=icd_verified,
                notes=(
                    "Used to verify every ICD-10-CM code in the SNOMED mapping and "
                    "every HCPCS Level II code. CPT codes are not in this service "
                    "(AMA licence), so their descriptors come from the CMS fee "
                    "schedules instead."
                ),
            )

    if total_failures:
        console.print(f"\n[red]{total_failures} mapping problem(s). Fix them before seeding.[/red]")
        raise SystemExit(1)
    console.print("\n[green]Every mapping verified.[/green]")


if __name__ == "__main__":
    main()
