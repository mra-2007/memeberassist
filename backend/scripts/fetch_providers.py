"""Load real providers for the project's city from the NPI Registry.

    uv run python manage.py fetch-providers

Names, NPIs, practice addresses, phone numbers and specialties are **real**
public data from the CMS NPI Registry. Only the network flag is ours.

Why the network flag needs a rule
---------------------------------
No public dataset says which providers are inside a given plan's network: that
is contract information each insurer publishes only in its own directory, in
formats that vary by issuer. So ``providers.in_network`` is assigned by a
documented, deterministic rule and ``providers.network_rule`` records which
rule produced each value, exactly as the claim status rules do:

* **Rule N1** -- in network when ``sha256(npi)[0] < 192``, i.e. about 75% of
  providers, which is a realistic network breadth for a county HMO or EPO.
* **Rule N2** -- the first provider in each specialty, ordered by NPI, is
  forced **out of network**, so every specialty can demonstrate an
  out-of-network answer and the cross-network tests always have a subject.

The rule is stable across runs because it depends only on the NPI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib

from rich.table import Table
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Claim, Provider, ProviderMemberLink
from app.db.session import session_scope
from app.external.npi import API_URL, ProviderRecord, search_providers
from scripts._common import console, print_row_counts, record_source, setup_logging

#: NPI taxonomy descriptions chosen to cover the procedure codes we price.
SPECIALTIES: tuple[tuple[str, str], ...] = (
    ("Family Medicine", "primary care visits (99203, 99213, 99214)"),
    ("Internal Medicine", "primary care and chronic condition management"),
    ("Orthopaedic Surgery", "knee imaging, arthroscopy and replacement (29881, 27447)"),
    ("Radiology", "MRI, CT, X-ray and ultrasound (73721, 72148, 74177)"),
    ("Physical Therapist", "outpatient rehabilitation (97161, 97110, 97140)"),
    ("Gastroenterology", "colonoscopy and endoscopy (45378, 43239)"),
    ("Cardiovascular Disease", "ECG and echocardiogram (93000, 93306)"),
    ("Ophthalmology", "cataract surgery (66984)"),
)

#: Target number of providers kept per specialty.
PER_SPECIALTY = 8

#: Threshold for rule N1. 192/256 = 75%.
IN_NETWORK_THRESHOLD = 192

LICENCE_NOTE = (
    "US federal government work, public domain. The NPI Registry is published "
    "by CMS for public use; provider names, NPIs and practice addresses are "
    "real and already public. MemberAssist adds no personal data to them."
)

RULE_N1 = "N1: in network when sha256(npi) first byte < 192 (about 75% of providers)"
RULE_N2 = "N2: lowest NPI in each specialty forced out of network for demo coverage"


def _in_network_by_hash(npi: str) -> bool:
    """Rule N1. Deterministic and dependent only on the NPI."""
    return hashlib.sha256(npi.encode()).digest()[0] < IN_NETWORK_THRESHOLD


def collect(state: str, city: str) -> dict[str, list[ProviderRecord]]:
    """Query the registry once per specialty and keep the verified matches."""
    found: dict[str, list[ProviderRecord]] = {}
    for taxonomy, purpose in SPECIALTIES:
        records = search_providers(taxonomy, city, state, limit=200)
        # De-duplicate by NPI, then take a deterministic slice.
        unique = {record["npi"]: record for record in records}
        chosen = sorted(unique.values(), key=lambda r: r["npi"])[:PER_SPECIALTY]
        found[taxonomy] = chosen
        marker = "ok" if chosen else "none"
        colour = "green" if chosen else "yellow"
        console.print(
            f"  [{colour}]{marker:>4}[/{colour}] {taxonomy:<24} {len(chosen):>2} providers "
            f"[dim]({purpose})[/dim]"
        )
    return found


def load(session: Session, *, state: str, city: str) -> int:
    """Fetch, flag and store providers. Returns the number stored."""
    console.print(f"[bold]Searching the NPI Registry for providers in {city}, {state}[/bold]")
    by_specialty = collect(state, city)
    total = sum(len(records) for records in by_specialty.values())
    if total == 0:
        raise SystemExit(f"No providers found in {city}, {state}; check the city name.")

    # Claims and provider links reference providers, so clear them first. At
    # this point in the pipeline they have not been built yet; the deletes keep
    # the script safe to re-run.
    session.execute(delete(ProviderMemberLink))
    session.execute(delete(Claim))
    session.execute(delete(Provider))

    retrieved = dt.date.today()
    stored = 0
    for taxonomy, records in by_specialty.items():
        for index, record in enumerate(sorted(records, key=lambda r: r["npi"])):
            forced_out = index == 0
            in_network = False if forced_out else _in_network_by_hash(record["npi"])
            session.add(
                Provider(
                    npi=record["npi"],
                    name=record["name"],
                    specialty=record["specialty"],
                    specialty_group=taxonomy,
                    taxonomy_code=record["taxonomy_code"],
                    address=record["address"],
                    city=record["city"],
                    state=record["state"],
                    zip_code=record["zip_code"],
                    phone=record["phone"],
                    in_network=in_network,
                    network_rule=RULE_N2 if forced_out else RULE_N1,
                    source="cms_npi_registry",
                    source_url=(f"{API_URL}?version=2.1&number={record['npi']}"),
                    retrieved_on=retrieved,
                )
            )
            stored += 1
    session.flush()

    record_source(
        session,
        key="cms_npi_registry",
        name="CMS National Provider Identifier (NPI) Registry, API version 2.1",
        url=API_URL,
        licence_note=LICENCE_NOTE,
        row_count=stored,
        notes=(
            f"Searched {len(SPECIALTIES)} taxonomies in {city}, {state}. Results are "
            "re-checked against each record's LOCATION address, because the "
            "registry's city filter also matches mailing addresses. "
            f"Network status is ours, not CMS's: {RULE_N1}; {RULE_N2}."
        ),
        retrieved_on=retrieved,
    )

    _print_summary(session)
    return stored


def _print_summary(session: Session) -> None:
    """Print providers per specialty with the in/out-of-network split."""
    rows = list(session.scalars(select(Provider).order_by(Provider.specialty_group, Provider.npi)))
    table = Table(title="Providers loaded (real NPIs; network flag is ours)")
    table.add_column("Specialty searched", overflow="fold")
    table.add_column("Total", justify="right")
    table.add_column("In network", justify="right")
    table.add_column("Out of network", justify="right")
    table.add_column("Sub-specialties", justify="right")
    grouped: dict[str, list[Provider]] = {}
    for provider in rows:
        grouped.setdefault(provider.specialty_group, []).append(provider)
    for specialty, providers in sorted(grouped.items()):
        inside = sum(1 for p in providers if p.in_network)
        table.add_row(
            specialty[:34],
            str(len(providers)),
            str(inside),
            str(len(providers) - inside),
            str(len({p.specialty for p in providers})),
        )
    console.print(table)
    console.print(
        f"  [dim]{sum(1 for p in rows if p.in_network)} of {len(rows)} flagged in network[/dim]"
    )
    example = next((p for p in rows if p.in_network), None)
    if example:
        console.print(
            f"  [dim]example: NPI {example.npi} — {example.name}, "
            f"{example.address}, {example.city} {example.zip_code}[/dim]"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="Dallas", help="Practice city to search.")
    parser.add_argument("--state", default=None, help="Two-letter state (default: PLAN_STATE).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    state = (args.state or get_settings().plan_state).upper()
    with session_scope() as session:
        stored = load(session, state=state, city=args.city)
        print_row_counts(session, "After fetch-providers")
    console.print(f"[green]Stored {stored} providers[/green]")


if __name__ == "__main__":
    main()
