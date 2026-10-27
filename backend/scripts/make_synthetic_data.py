"""Build the synthetic member population, claims, accumulators and demo users.

    uv run python manage.py make-data

Everything this script writes is **synthetic and labelled as such** -- every
row carries ``is_synthetic = true``. The people do not exist. What is real is
the structure around them: the plans and their cost sharing (CMS filings), the
prices (CMS fee schedules), the providers (NPI Registry) and the clinical
coding (Synthea's SNOMED output mapped to ICD-10 codes verified against NLM).

Source population
-----------------
Synthea's pre-generated 10,000-patient CSV sample. Synthea itself is not run
here because Java is not installed on this machine, which the specification
expressly allows. The smaller "latest" sample was rejected: it holds 111
patients, only 77 of them living adults, too few for the ~200 the project
wants.

Documented transformations, all deterministic from ``RANDOM_SEED``
------------------------------------------------------------------
**T1 re-homing.** Synthea's sample is Massachusetts, which runs its own
exchange, so members are re-homed to the project's county. The street line is
kept; city, state and ZIP are replaced with real Dallas values.

**T2 contact details.** Synthea emits no phone or email. Numbers use the
``555-01xx`` block reserved for fiction and addresses use the ``.invalid``
top-level domain from RFC 2606, so neither can ever reach a real person.

**T3 names.** Synthea appends digits to names ("Schiller186") to mark them
synthetic. The digits are stripped for readability; ``is_synthetic`` and the
UI badge carry that signal instead.

**T4 date shift.** The sample's encounters end around 2020. Each member's
history is shifted forward as a whole, preserving every interval, so their
most recent encounter lands inside the current plan year. The shift is
recorded per member in the data-source notes.

**T5 procedure assignment.** Synthea encounters carry SNOMED encounter and
reason codes, not billing codes. Each encounter is assigned one of our priced
HCPCS codes by the table in ``PROCEDURE_BY_ENCOUNTER``, keyed on encounter
class and the condition category behind it.

**T6 billed amount.** Real claims bill above the allowed amount. The billed
figure is ``allowed x factor`` where the factor is drawn from 1.8-3.2 by a
hash of the claim id, so it is stable across runs. This is the one invented
number in the claim; ``claims.status_rule`` and this docstring are where that
is admitted.

Claim status rules (specification section 7.6), recorded on every claim
----------------------------------------------------------------------
======================  =================================================
``PA_MISSING``          a PA-required code with no approved prior authorization
``OUT_OF_NETWORK``      an HMO or EPO plan plus an out-of-network provider
``NOT_COVERED``         the plan's filing excludes the service
``DUPLICATE``           same member, same code, same date as an earlier claim
otherwise               paid, or pending when the service date is recent
======================  =================================================
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import random
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from rich.table import Table
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import DATA_DIR, get_settings
from app.db.models import (
    Accumulator,
    Claim,
    Member,
    PaRequest,
    Plan,
    PlanBenefit,
    ProcedurePrice,
    Provider,
    ProviderMemberLink,
    Refill,
    User,
)
from app.db.session import session_scope
from app.external.cache import fetch_bytes
from app.services.cost_sharing import (
    Accumulators,
    BenefitRule,
    money,
    settle,
    settle_out_of_network,
)
from scripts._common import RANDOM_SEED, console, print_row_counts, record_source, setup_logging
from scripts._common import money as fmt_money

SYNTHEA_URL = (
    "https://raw.githubusercontent.com/synthetichealth/synthea-sample-data/main/"
    "downloads/10k_synthea_covid19_csv.zip"
)
SYNTHEA_CREDIT = "https://synthetichealth.github.io/synthea-sample-data/"
SYNTHEA_LICENCE = (
    "Synthea is open source (Apache License 2.0) from The MITRE Corporation. "
    "Its output is entirely synthetic: no real patient data is involved. "
    "MemberAssist uses the pre-generated public sample unmodified as input."
)

#: Target population size, plus the two member IDs the demo script names.
POPULATION = 200
HERO_MEMBER_ID = "M10234"
OTHER_MEMBER_ID = "M10555"
DEMO_PA_ID = "PA-889"
DEMO_CLAIM_ID = "CLM-4521"
#: The hero must have no approved prior authorization for the knee MRI, because
#: the demo's claim for it is denied PA_MISSING.
DEMO_DENIED_PA_CODES = frozenset({"73721"})

#: Real ZIP codes inside Dallas County, Texas (FIPS 48113).
DALLAS_ZIPS = (
    "75201",
    "75202",
    "75203",
    "75204",
    "75205",
    "75206",
    "75208",
    "75209",
    "75214",
    "75218",
    "75219",
    "75220",
    "75223",
    "75224",
    "75225",
    "75226",
    "75228",
    "75229",
    "75230",
    "75231",
    "75235",
    "75238",
    "75240",
    "75243",
    "75246",
    "75247",
    "75248",
    "75251",
    "75252",
    "75254",
)
#: Dallas area codes, used with the fiction-reserved 555-01xx exchange.
AREA_CODES = ("214", "972", "469")

#: T5: which procedure code a Synthea encounter becomes.
#: Keyed on (encounter class, condition category); ``None`` category is the
#: fallback when the encounter has no mapped reason code.
PROCEDURE_BY_ENCOUNTER: dict[tuple[str, str | None], str] = {
    ("wellness", None): "99213",
    ("ambulatory", None): "99213",
    ("ambulatory", "musculoskeletal"): "97110",
    ("ambulatory", "endocrine"): "83036",
    ("ambulatory", "circulatory"): "93000",
    ("ambulatory", "respiratory"): "99214",
    ("ambulatory", "mental"): "90834",
    ("ambulatory", "genitourinary"): "80053",
    ("ambulatory", "neoplasm"): "99214",
    # Two services every selected plan excludes, so NOT_COVERED denials and
    # "is this covered?" answers are exercised by real plan exclusions.
    ("ambulatory", "nervous"): "97810",
    ("inpatient", "endocrine"): "43644",
    ("outpatient", None): "99214",
    ("outpatient", "musculoskeletal"): "73721",
    ("outpatient", "injury"): "73560",
    ("outpatient", "circulatory"): "93306",
    ("outpatient", "endocrine"): "80053",
    ("outpatient", "eye"): "66984",
    ("outpatient", "neoplasm"): "74177",
    ("outpatient", "genitourinary"): "45378",
    ("outpatient", "nervous"): "70551",
    ("urgentcare", None): "99203",
    ("urgentcare", "injury"): "71046",
    ("emergency", None): "99285",
    ("emergency", "injury"): "99284",
    ("inpatient", None): "99285",
    ("inpatient", "musculoskeletal"): "27447",
    ("virtual", None): "99213",
}

#: Claims per member are capped so the demo database stays small and fast.
MAX_CLAIMS_PER_MEMBER = 10

#: A claim newer than this many days is still working through adjudication.
PENDING_WINDOW_DAYS = 21

#: Plan types that do not cover out-of-network care outside emergencies.
NETWORK_RESTRICTED_PLAN_TYPES = frozenset({"HMO", "EPO"})

#: Share of prior-authorization-required claims that have an approved PA on
#: file. The rest are denied PA_MISSING, which is the denial the demo shows.
PA_APPROVAL_SHARE = Decimal("0.7")

STATUS_RULES = {
    "PA_MISSING": "PA_MISSING: procedure requires prior authorization and no approved PA exists",
    "OUT_OF_NETWORK": "OUT_OF_NETWORK: plan type {plan_type} does not cover out-of-network care and provider {npi} is out of network",
    "NOT_COVERED": "NOT_COVERED: the plan's filing marks {benefit} as not covered",
    "DUPLICATE": "DUPLICATE: same member, procedure {code} and service date {date} as claim {other}",
    "PENDING": "PENDING: service date {date} is within {days} days, still adjudicating",
    "PAID": "PAID: covered in network, member share calculated from the plan's cost sharing",
}


@dataclass(slots=True)
class SyntheaPatient:
    """One living adult from the Synthea sample."""

    synthea_id: str
    first: str
    last: str
    birthdate: dt.date
    gender: str
    address: str


@dataclass(slots=True)
class SyntheaEncounter:
    """One encounter, with the reason code that explains it."""

    patient: str
    start: dt.date
    encounter_class: str
    reason_code: str


# ---------------------------------------------------------------------------
# reading the Synthea sample
# ---------------------------------------------------------------------------
def _strip_digits(name: str) -> str:
    """T3: 'Schiller186' -> 'Schiller'."""
    return "".join(char for char in name if not char.isdigit()).strip() or name


def _open_member(archive: zipfile.ZipFile, filename: str) -> csv.DictReader:
    """A streaming DictReader over one CSV inside the Synthea archive."""
    name = next(entry for entry in archive.namelist() if entry.lower().endswith(filename))
    handle = archive.open(name)
    return csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace"))


def _age_on(birthdate: dt.date, when: dt.date) -> int:
    return when.year - birthdate.year - ((when.month, when.day) < (birthdate.month, birthdate.day))


#: SNOMED code for knee osteoarthritis. The demo's hero member must really
#: have it, so the hero is picked from patients who do rather than having the
#: diagnosis bolted on.
KNEE_OSTEOARTHRITIS_SNOMED = "239873007"


@dataclass(slots=True)
class SyntheaSample:
    """The selected population and the history belonging to it."""

    patients: list[SyntheaPatient]
    hero: SyntheaPatient
    encounters: dict[str, list[SyntheaEncounter]]
    conditions: dict[str, set[str]]
    medications: dict[str, list[dict[str, Any]]]
    adults_available: int


def load_synthea(count: int, today: dt.date) -> SyntheaSample:
    """Read the sample and return the selected adults with their history."""
    console.print("[bold]Reading the Synthea 10,000-patient sample[/bold]")
    archive = zipfile.ZipFile(io.BytesIO(fetch_bytes(SYNTHEA_URL)))

    adults: dict[str, SyntheaPatient] = {}
    for row in _open_member(archive, "patients.csv"):
        if row.get("DEATHDATE", "").strip():
            continue
        try:
            birthdate = dt.date.fromisoformat(row["BIRTHDATE"])
        except (KeyError, ValueError):
            continue
        if not 18 <= _age_on(birthdate, today) <= 90:
            continue
        adults[row["Id"]] = SyntheaPatient(
            synthea_id=row["Id"],
            first=_strip_digits(row.get("FIRST", "")),
            last=_strip_digits(row.get("LAST", "")),
            birthdate=birthdate,
            gender=row.get("GENDER", "").strip() or "unknown",
            address=row.get("ADDRESS", "").strip(),
        )
    console.print(f"  {len(adults):,} living adults aged 18-90 in the sample")

    # Conditions for every adult, so the hero can be chosen on diagnosis.
    all_conditions: dict[str, set[str]] = defaultdict(set)
    for row in _open_member(archive, "conditions.csv"):
        patient = row.get("PATIENT", "")
        if patient in adults:
            all_conditions[patient].add(row.get("CODE", "").strip())

    knee_oa = sorted(
        pid for pid, codes in all_conditions.items() if KNEE_OSTEOARTHRITIS_SNOMED in codes
    )
    if not knee_oa:
        raise SystemExit(
            "No living adult in the sample has knee osteoarthritis "
            f"(SNOMED {KNEE_OSTEOARTHRITIS_SNOMED}); the demo scenario needs one."
        )

    # Deterministic selection: the knee-osteoarthritis candidates plus a
    # random sample of everyone else. The hero is chosen from the candidates
    # after their encounter history is known.
    rest = sorted(pid for pid in adults if pid not in set(knee_oa))
    rng = random.Random(RANDOM_SEED)
    picked = rng.sample(rest, min(max(count - len(knee_oa), 0), len(rest)))
    chosen_ids = set(knee_oa) | set(picked)

    encounters: dict[str, list[SyntheaEncounter]] = defaultdict(list)
    for row in _open_member(archive, "encounters.csv"):
        patient = row.get("PATIENT", "")
        if patient not in chosen_ids:
            continue
        try:
            start_date = dt.date.fromisoformat(row.get("START", "")[:10])
        except ValueError:
            continue
        encounters[patient].append(
            SyntheaEncounter(
                patient=patient,
                start=start_date,
                encounter_class=row.get("ENCOUNTERCLASS", "").strip(),
                reason_code=row.get("REASONCODE", "").strip(),
            )
        )

    # The hero needs a believable recent history: varied enough that the demo
    # has different things to ask about, and not so dense that the claim list
    # is the same office visit ten times. The highest-utilisation patient in
    # the sample has 148 encounters in a year, which reads as a bug on screen.
    def final_year(pid: str) -> list[SyntheaEncounter]:
        history = encounters.get(pid, [])
        if not history:
            return []
        cutoff = max(entry.start for entry in history) - dt.timedelta(days=365)
        return [entry for entry in history if entry.start >= cutoff]

    def hero_score(pid: str) -> tuple[int, int, str]:
        recent = final_year(pid)
        variety = len({entry.encounter_class for entry in recent})
        # Prefer roughly a claim a month: enough to discuss, not a wall.
        closeness = -abs(len(recent) - MAX_CLAIMS_PER_MEMBER)
        return (variety, closeness, pid)

    hero_id = max(knee_oa, key=hero_score)
    hero = adults[hero_id]
    recent = final_year(hero_id)
    console.print(
        f"  {len(knee_oa)} adults have knee osteoarthritis; hero is "
        f"{hero.first} {hero.last} with {len(recent)} encounters across "
        f"{len({e.encounter_class for e in recent})} encounter types in their final year"
    )

    # Trim to the requested population, always keeping the hero.
    keep = [hero_id, *sorted(pid for pid in chosen_ids if pid != hero_id)][:count]
    chosen_ids = set(keep)
    selected = [adults[hero_id], *(adults[pid] for pid in keep[1:])]
    encounters = defaultdict(
        list, {pid: encounters[pid] for pid in chosen_ids if pid in encounters}
    )
    conditions = {pid: all_conditions.get(pid, set()) for pid in chosen_ids}
    console.print(f"  selected {len(selected)} members with seed {RANDOM_SEED}")

    medications: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _open_member(archive, "medications.csv"):
        patient = row.get("PATIENT", "")
        if patient in chosen_ids and row.get("CODE", "").strip():
            medications[patient].append(row)

    console.print(
        f"  read {sum(len(v) for v in encounters.values()):,} encounters, "
        f"{sum(len(v) for v in conditions.values()):,} conditions, "
        f"{sum(len(v) for v in medications.values()):,} medication records"
    )
    return SyntheaSample(
        patients=selected,
        hero=hero,
        encounters=encounters,
        conditions=conditions,
        medications=medications,
        adults_available=len(adults),
    )


# ---------------------------------------------------------------------------
# mappings
# ---------------------------------------------------------------------------
def load_condition_map() -> dict[str, dict[str, str]]:
    """SNOMED -> verified ICD-10 mapping, claimable entries flagged."""
    path = DATA_DIR / "mappings" / "snomed_to_icd10.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    unverified = [row["icd10_code"] for row in rows if not row.get("verified_title")]
    if unverified:
        raise SystemExit(
            "These ICD-10 codes are not verified: "
            f"{', '.join(unverified)}. Run `manage.py verify-mappings` first."
        )
    return {row["snomed_code"]: row for row in rows}


def load_code_benefits() -> dict[str, dict[str, str]]:
    """HCPCS -> benefit category, default setting and PA flag."""
    path = DATA_DIR / "mappings" / "code_to_benefit.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["hcpcs_code"]: row for row in csv.DictReader(handle)}


# ---------------------------------------------------------------------------
# deterministic helpers
# ---------------------------------------------------------------------------
def _hash_fraction(*parts: str) -> Decimal:
    """A stable number in [0, 1) derived from the given strings."""
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    value = int.from_bytes(digest[:6], "big")
    return Decimal(value) / Decimal(1 << 48)


def billed_from_allowed(allowed: Decimal, claim_id: str) -> Decimal:
    """T6: a stable billed charge above the allowed amount (1.8x to 3.2x)."""
    factor = Decimal("1.8") + _hash_fraction("billed", claim_id) * Decimal("1.4")
    return money(allowed * factor)


def _ascii_local_part(text: str) -> str:
    """Fold a name to ASCII for an email local part.

    Synthea names include accented characters ("Tomas" is emitted as
    "Tomás"), and a non-ASCII local part is only valid over SMTPUTF8. The
    stored name keeps its accents; only the address is folded.
    """
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return "".join(char for char in stripped.lower() if char.isalnum() or char in "._-")


def _contact_details(index: int, first: str, last: str) -> tuple[str, str]:
    """T2: a phone number and email that can never reach a real person."""
    area = AREA_CODES[index % len(AREA_CODES)]
    phone = f"({area}) 555-01{index % 100:02d}"
    local = f"{_ascii_local_part(first)}.{_ascii_local_part(last)}".strip(".")
    email = f"{local or 'member'}{index}@example.invalid"
    return phone, email


# ---------------------------------------------------------------------------
# which provider sees which procedure
# ---------------------------------------------------------------------------
#: Procedure code -> the NPI taxonomy that performs it, for provider choice.
SPECIALTY_BY_CODE: dict[str, str] = {
    "99203": "Family Medicine",
    "99213": "Family Medicine",
    "99214": "Internal Medicine",
    "99284": "Family Medicine",
    "99285": "Family Medicine",
    "73721": "Radiology",
    "72148": "Radiology",
    "70551": "Radiology",
    "74177": "Radiology",
    "77067": "Radiology",
    "71046": "Radiology",
    "73560": "Radiology",
    "76700": "Radiology",
    "93306": "Cardiovascular Disease",
    "93000": "Cardiovascular Disease",
    "29881": "Orthopaedic Surgery",
    "27447": "Orthopaedic Surgery",
    "20610": "Orthopaedic Surgery",
    "64483": "Orthopaedic Surgery",
    "97110": "Physical Therapist",
    "97140": "Physical Therapist",
    "97161": "Physical Therapist",
    "45378": "Gastroenterology",
    "43239": "Gastroenterology",
    "66984": "Ophthalmology",
    "80053": "Internal Medicine",
    "85025": "Internal Medicine",
    "83036": "Internal Medicine",
    "80061": "Internal Medicine",
    "36415": "Internal Medicine",
    "90834": "Internal Medicine",
    "90471": "Family Medicine",
}


#: T7: share of claims where the member went out of network. Members mostly
#: use their plan's network, so choosing providers uniformly would deny a
#: realistic plan's claims far too often. One claim in eight is out of network,
#: which keeps OUT_OF_NETWORK denials present but not dominant.
OUT_OF_NETWORK_SHARE = Decimal("0.125")


def pick_provider(
    providers_by_group: dict[str, list[Provider]],
    all_providers: list[Provider],
    code: str,
    key: str,
) -> Provider:
    """Choose a provider deterministically: right specialty, usually in network.

    See ``OUT_OF_NETWORK_SHARE`` for why the network tier is weighted rather
    than drawn uniformly.
    """
    group = SPECIALTY_BY_CODE.get(code)
    pool = providers_by_group.get(group or "", []) or all_providers
    wants_out = _hash_fraction("network", key, code) < OUT_OF_NETWORK_SHARE
    preferred = [provider for provider in pool if provider.in_network is not wants_out]
    candidates = preferred or pool
    index = int(_hash_fraction("provider", key, code) * len(candidates))
    return candidates[min(index, len(candidates) - 1)]


# ---------------------------------------------------------------------------
# claim building
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class PriceLookup:
    """Prices indexed by code and place of service."""

    rows: dict[tuple[str, str], ProcedurePrice]

    def best(self, code: str, preferred_setting: str) -> ProcedurePrice | None:
        """The price for a code, falling back to the other setting."""
        other = "facility" if preferred_setting == "office" else "office"
        return self.rows.get((code, preferred_setting)) or self.rows.get((code, other))


def date_shift_for(
    patient: SyntheaPatient, encounters: list[SyntheaEncounter], today: dt.date
) -> dt.timedelta:
    """T4: shift a member's whole history so it ends inside the plan year.

    Intervals between encounters are preserved exactly; only the origin moves.
    The gap between the newest encounter and today is 3-120 days, chosen
    deterministically per member so claims are not all dated the same day.
    """
    latest = max(encounter.start for encounter in encounters)
    gap = 3 + int(_hash_fraction("shift", patient.synthea_id) * 118)
    return (today - dt.timedelta(days=gap)) - latest


#: Encounter classes where the specific procedure depends on the diagnosis.
SPECIALIST_CLASSES = frozenset({"outpatient", "inpatient"})


def category_for(
    reason_code: str,
    condition_map: dict[str, dict[str, str]],
    *,
    encounter_class: str = "",
    member_conditions: set[str] | None = None,
) -> str | None:
    """The condition category behind an encounter.

    Synthea leaves ``REASONCODE`` empty on many encounters. For a routine
    visit that does not matter -- it becomes an office visit either way. For an
    outpatient or inpatient encounter it does: without a category every such
    encounter would collapse to a generic visit code, so no imaging, surgery
    or prior authorization would ever appear. In that case the member's own
    chronic condition supplies the category, which is also how a real referral
    works.
    """
    row = condition_map.get(reason_code)
    if row is not None and row.get("category"):
        return row["category"]
    if encounter_class not in SPECIALIST_CLASSES or not member_conditions:
        return None
    for snomed in sorted(member_conditions):
        candidate = condition_map.get(snomed)
        if candidate and candidate.get("claimable") == "true" and candidate.get("category"):
            return candidate["category"]
    return None


def procedure_for(encounter_class: str, category: str | None) -> str | None:
    """T5: which priced procedure code an encounter becomes."""
    return PROCEDURE_BY_ENCOUNTER.get((encounter_class, category)) or PROCEDURE_BY_ENCOUNTER.get(
        (encounter_class, None)
    )


def diagnoses_for(
    reason_code: str,
    member_conditions: set[str],
    condition_map: dict[str, dict[str, str]],
) -> list[str]:
    """ICD-10 codes for a claim: the encounter reason first, then a chronic one."""
    codes: list[str] = []
    reason = condition_map.get(reason_code)
    if reason and reason.get("claimable") == "true":
        codes.append(reason["icd10_code"])
    for snomed in sorted(member_conditions):
        row = condition_map.get(snomed)
        if row is None or row.get("claimable") != "true":
            continue
        code = row["icd10_code"]
        if code not in codes:
            codes.append(code)
        if len(codes) >= 2:
            break
    return codes


# ---------------------------------------------------------------------------
# building members
# ---------------------------------------------------------------------------
def member_ids(count: int) -> list[str]:
    """Member IDs for the population.

    The specification numbers members from ``M10001`` and separately names
    ``M10234`` (the demo hero) and ``M10555`` (the "other member" the attack
    tests try to read). ``M10555`` falls outside a 200-long run, so the two
    demo IDs are reserved explicitly and the rest fill the sequence around
    them. Every ID is therefore either sequential or one the demo script names.
    """
    reserved = [HERO_MEMBER_ID, OTHER_MEMBER_ID]
    sequential = []
    index = 1
    while len(sequential) < count - len(reserved):
        candidate = f"M{10000 + index:05d}"
        if candidate not in reserved:
            sequential.append(candidate)
        index += 1
    return [HERO_MEMBER_ID, OTHER_MEMBER_ID, *sequential]


def choose_hero_plan(plans: list[Plan], benefits: dict[tuple[str, str, str], PlanBenefit]) -> Plan:
    """The hero's plan must make the demo's cost question interesting.

    It needs advanced imaging priced as coinsurance after the deductible, and
    out-of-network coverage so the demo's "what about out of network?"
    follow-up has a real answer. Among those, the largest deductible is chosen
    so that "part of your deductible is used" is a meaningful statement.
    """
    imaging = "Imaging (CT/PET Scans, MRIs)"
    candidates = []
    for plan in plans:
        rule = benefits.get((plan.plan_id, imaging, "in_network"))
        if rule is None or not rule.is_covered:
            continue
        if not (rule.after_deductible and rule.coinsurance and rule.coinsurance > 0):
            continue
        covers_out = benefits.get((plan.plan_id, imaging, "out_of_network"))
        candidates.append(
            (
                0 if covers_out and covers_out.is_covered else 1,
                -plan.deductible_ind,
                plan.plan_id,
                plan,
            )
        )
    if not candidates:
        raise SystemExit(
            "No plan prices imaging as coinsurance after the deductible; re-run fetch-plans."
        )
    return min(candidates)[3]


def build_members(
    session: Session,
    sample: SyntheaSample,
    plans: list[Plan],
    hero_plan: Plan,
    settings: Any,
) -> dict[str, Member]:
    """Create the member rows. Returns them keyed by member ID."""
    ids = member_ids(len(sample.patients))
    # The hero takes M10234. M10555 comes next, because the attack suite tries
    # to read that member's records: a refusal is only convincing when there is
    # something real behind it, so the member with the next richest history
    # takes that ID. Everyone else follows in Synthea id order.
    rest = [p for p in sample.patients if p.synthea_id != sample.hero.synthea_id]
    rest.sort(key=lambda p: (-len(sample.encounters.get(p.synthea_id, [])), p.synthea_id))
    ordered = [sample.hero, *rest[:1], *sorted(rest[1:], key=lambda p: p.synthea_id)]
    others = [plan for plan in plans if plan.plan_id != hero_plan.plan_id] or plans

    created: dict[str, Member] = {}
    for position, (member_id, patient) in enumerate(zip(ids, ordered, strict=False)):
        phone, email = _contact_details(position, patient.first, patient.last)
        zip_code = DALLAS_ZIPS[int(_hash_fraction("zip", patient.synthea_id) * len(DALLAS_ZIPS))]
        plan = hero_plan if member_id == HERO_MEMBER_ID else others[position % len(others)]
        member = Member(
            member_id=member_id,
            first_name=patient.first,
            last_name=patient.last,
            dob=patient.birthdate,
            gender=patient.gender,
            phone=phone,
            email=email,
            address=patient.address,
            city="Dallas",
            state=settings.plan_state,
            zip_code=zip_code,
            plan_id=plan.plan_id,
            synthea_id=patient.synthea_id,
            is_synthetic=True,
        )
        session.add(member)
        created[member_id] = member
    session.flush()
    return created


# ---------------------------------------------------------------------------
# building claims
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ClaimPlan:
    """A claim about to be written, before adjudication."""

    claim_id: str
    member: Member
    plan: Plan
    service_date: dt.date
    hcpcs_code: str
    place_of_service: str
    provider: Provider
    icd10_codes: list[str]
    benefit_name: str
    pa_required: bool


def _rule_from_row(row: PlanBenefit | None, benefit_name: str, tier: str) -> BenefitRule:
    """Turn a stored plan_benefits row into a cost-sharing rule."""
    if row is None:
        return BenefitRule(
            benefit_name=benefit_name,
            network_tier=tier,
            copay=None,
            coinsurance=None,
            after_deductible=False,
            is_covered=False,
            display_text="Not listed in this plan's filing",
        )
    return BenefitRule(
        benefit_name=row.benefit_name,
        network_tier=row.network_tier,
        copay=row.copay,
        coinsurance=row.coinsurance,
        after_deductible=row.after_deductible,
        is_covered=row.is_covered,
        display_text=row.display_text,
    )


def plan_claims_for_member(
    member: Member,
    plan: Plan,
    patient: SyntheaPatient,
    encounters: list[SyntheaEncounter],
    member_conditions: set[str],
    condition_map: dict[str, dict[str, str]],
    code_benefits: dict[str, dict[str, str]],
    prices: PriceLookup,
    providers_by_group: dict[str, list[Provider]],
    all_providers: list[Provider],
    today: dt.date,
    plan_year: int,
) -> list[ClaimPlan]:
    """Turn one member's shifted encounters into claims, newest last."""
    if not encounters:
        return []
    shift = date_shift_for(patient, encounters, today)
    year_start = dt.date(plan_year, 1, 1)

    candidates: list[ClaimPlan] = []
    for encounter in sorted(encounters, key=lambda e: e.start):
        service_date = encounter.start + shift
        if not year_start <= service_date <= today:
            continue
        category = category_for(
            encounter.reason_code,
            condition_map,
            encounter_class=encounter.encounter_class,
            member_conditions=member_conditions,
        )
        code = procedure_for(encounter.encounter_class, category)
        if code is None:
            continue
        mapping = code_benefits.get(code)
        if mapping is None:
            continue
        price = prices.best(code, mapping["place_of_service"])
        if price is None:
            continue
        serial = int(
            _hash_fraction("claim", member.member_id, code, service_date.isoformat()) * 9000
        )
        candidates.append(
            ClaimPlan(
                claim_id=f"CLM-{serial + 1000}",
                member=member,
                plan=plan,
                service_date=service_date,
                hcpcs_code=code,
                place_of_service=price.place_of_service,
                provider=pick_provider(
                    providers_by_group,
                    all_providers,
                    code,
                    f"{member.member_id}{service_date.isoformat()}",
                ),
                icd10_codes=diagnoses_for(encounter.reason_code, member_conditions, condition_map),
                benefit_name=mapping["benefit_name"],
                pa_required=mapping.get("pa_commonly_required") == "true",
            )
        )
    # Keep the most recent claims; older history is not what members ask about.
    return sorted(candidates, key=lambda c: c.service_date)[-MAX_CLAIMS_PER_MEMBER:]


def _as_datetime(date: dt.date) -> dt.datetime:
    """Midday UTC on a date, so timezone handling never shifts the day."""
    return dt.datetime.combine(date, dt.time(12, 0), tzinfo=dt.UTC)


def adjudicate(
    planned: list[ClaimPlan],
    plan: Plan,
    benefits: dict[tuple[str, str, str], PlanBenefit],
    prices: PriceLookup,
    today: dt.date,
    used_claim_ids: set[str],
    pa_counter: Counter,
    denied_pa_codes: frozenset[str] = frozenset(),
) -> tuple[list[Claim], list[PaRequest], Decimal, Decimal]:
    """Apply the status rules and the cost sharing, in service-date order.

    Rule precedence is the order of the specification's table: PA_MISSING,
    OUT_OF_NETWORK, NOT_COVERED, DUPLICATE, then paid or pending.

    Returns:
        ``(claims, pa_requests, deductible_used, oop_used)``.
    """
    deductible_left = plan.deductible_ind
    oop_left = plan.oop_max_ind
    deductible_used = Decimal("0.00")
    oop_used = Decimal("0.00")
    seen: dict[tuple[str, str], str] = {}
    claims: list[Claim] = []
    pa_requests: list[PaRequest] = []
    # One prior-authorization decision per procedure code, per member.
    # ``denied_pa_codes`` pre-sets a refusal, which the demo scenario needs so
    # the hero cannot hold an approved knee-MRI PA and a PA_MISSING denial for
    # a knee MRI at the same time.
    pa_decisions: dict[str, str | None] = dict.fromkeys(denied_pa_codes)
    network_restricted = plan.plan_type.upper() in NETWORK_RESTRICTED_PLAN_TYPES

    for item in planned:
        claim_id = item.claim_id
        suffix = 0
        while claim_id in used_claim_ids:
            suffix += 1
            claim_id = f"{item.claim_id}-{suffix}"
        used_claim_ids.add(claim_id)

        price = prices.best(item.hcpcs_code, item.place_of_service)
        if price is None:
            continue
        allowed = price.avg_allowed
        billed = billed_from_allowed(allowed, claim_id)

        tier = "in_network" if item.provider.in_network else "out_of_network"
        rule = _rule_from_row(
            benefits.get((plan.plan_id, item.benefit_name, tier)), item.benefit_name, tier
        )

        status: str | None = None
        denial_code: str | None = None
        denial_reason: str | None = None
        status_rule = ""
        pa_id: str | None = None

        if item.pa_required:
            # A prior authorization is granted to a member for a service, not
            # to one claim, so the decision is made once per procedure code and
            # every claim for that code in the plan year inherits it. Deciding
            # per claim would let a member hold an approved PA for a knee MRI
            # and still be denied for a knee MRI, which no member would accept.
            if item.hcpcs_code not in pa_decisions:
                approved = _hash_fraction("pa", item.member.member_id, item.hcpcs_code)
                if approved < PA_APPROVAL_SHARE:
                    # A counter rather than a hash: PA ids must be unique, and
                    # the claim order is already deterministic.
                    pa_counter["n"] += 1
                    granted = f"PA-{1000 + pa_counter['n']}"
                    pa_requests.append(
                        PaRequest(
                            pa_id=granted,
                            member_id=item.member.member_id,
                            provider_npi=item.provider.npi,
                            hcpcs_code=item.hcpcs_code,
                            icd10_codes=item.icd10_codes,
                            status="APPROVED",
                            submitted_at=_as_datetime(item.service_date - dt.timedelta(days=14)),
                            decided_at=_as_datetime(item.service_date - dt.timedelta(days=7)),
                            missing_items=[],
                            notes_masked=None,
                            is_synthetic=True,
                        )
                    )
                    pa_decisions[item.hcpcs_code] = granted
                else:
                    pa_decisions[item.hcpcs_code] = None

            pa_id = pa_decisions[item.hcpcs_code]
            if pa_id is None:
                status, denial_code = "DENIED", "PA_MISSING"
                denial_reason = (
                    "This service needs prior approval from your plan before it is "
                    "done, and an approved request had not reached us by the "
                    "service date."
                )
                status_rule = STATUS_RULES["PA_MISSING"]

        if status is None and network_restricted and tier == "out_of_network":
            status, denial_code = "DENIED", "OUT_OF_NETWORK"
            denial_reason = (
                f"Your {plan.plan_type} plan covers care from providers in its network "
                "only, apart from emergencies, and this provider is outside it."
            )
            status_rule = STATUS_RULES["OUT_OF_NETWORK"].format(
                plan_type=plan.plan_type, npi=item.provider.npi
            )

        if status is None and not rule.is_covered:
            where = " outside its network" if tier == "out_of_network" else ""
            status, denial_code = "DENIED", "NOT_COVERED"
            denial_reason = (
                f"Your plan's benefits list does not cover {item.benefit_name.lower()}{where}."
            )
            status_rule = STATUS_RULES["NOT_COVERED"].format(benefit=item.benefit_name)

        duplicate_key = (item.hcpcs_code, item.service_date.isoformat())
        if status is None and duplicate_key in seen:
            status, denial_code = "DENIED", "DUPLICATE"
            denial_reason = (
                "We already have a claim for the same service on the same day, so "
                "this one was closed as a duplicate."
            )
            status_rule = STATUS_RULES["DUPLICATE"].format(
                code=item.hcpcs_code, date=item.service_date, other=seen[duplicate_key]
            )
        seen.setdefault(duplicate_key, claim_id)

        applied = Decimal("0.00")
        oop_credit = Decimal("0.00")
        if status == "DENIED":
            # A denied claim leaves the member billed and nothing accumulates.
            plan_paid = Decimal("0.00")
            member_resp = billed
        elif (today - item.service_date).days <= PENDING_WINDOW_DAYS:
            status = "PENDING"
            status_rule = STATUS_RULES["PENDING"].format(
                date=item.service_date, days=PENDING_WINDOW_DAYS
            )
            plan_paid = Decimal("0.00")
            member_resp = Decimal("0.00")
        else:
            status = "PAID"
            status_rule = STATUS_RULES["PAID"]
            accumulators = Accumulators(deductible_left, oop_left)
            if tier == "in_network":
                result = settle(allowed, rule, accumulators)
                member_resp = result.member_pays
                plan_paid = result.plan_pays
                applied = result.applied_to_deductible
                oop_credit = result.member_pays
            else:
                # Out of network the plan applies its cost sharing to the
                # allowed amount and the provider bills the member the
                # difference; that balance counts toward neither accumulator.
                out = settle_out_of_network(allowed, billed, rule, accumulators)
                member_resp = out.member_pays
                plan_paid = out.plan_pays
                applied = out.applied_to_deductible
                oop_credit = out.counts_toward_oop
            deductible_left = money(max(deductible_left - applied, Decimal("0.00")))
            oop_left = money(max(oop_left - oop_credit, Decimal("0.00")))
            deductible_used = money(deductible_used + applied)
            oop_used = money(oop_used + oop_credit)

        claims.append(
            Claim(
                claim_id=claim_id,
                member_id=item.member.member_id,
                provider_npi=item.provider.npi,
                service_date=item.service_date,
                icd10_codes=item.icd10_codes,
                hcpcs_code=item.hcpcs_code,
                place_of_service=item.place_of_service,
                billed=billed,
                allowed=allowed,
                plan_paid=plan_paid,
                member_resp=member_resp,
                status=status,
                denial_code=denial_code,
                denial_reason=denial_reason,
                status_rule=status_rule,
                pa_id=pa_id,
                applied_to_deductible=applied,
                applied_to_oop=oop_credit,
                is_synthetic=True,
            )
        )
    return claims, pa_requests, deductible_used, oop_used


# ---------------------------------------------------------------------------
# the demo scenario
# ---------------------------------------------------------------------------
def install_demo_scenario(
    session: Session,
    hero: Member,
    plan: Plan,
    benefits: dict[tuple[str, str, str], PlanBenefit],
    prices: PriceLookup,
    providers_by_group: dict[str, list[Provider]],
    today: dt.date,
    used_claim_ids: set[str],
) -> dict[str, str]:
    """Create the exact rows the demo script walks through.

    The demo needs a specific, findable claim and prior authorization. Both are
    built from the same real plan rules and real prices as every other claim --
    only their identifiers and outcome are fixed, so the figures on screen are
    the plan's own and nothing is invented to fit the script.
    """
    imaging_benefit = "Imaging (CT/PET Scans, MRIs)"
    knee_mri = "73721"
    price = prices.best(knee_mri, "office")
    if price is None:
        raise SystemExit("No price for the knee MRI (73721); re-run fetch-prices.")

    radiologists = providers_by_group.get("Radiology", [])
    in_network = [provider for provider in radiologists if provider.in_network]
    if not in_network:
        raise SystemExit("No in-network radiologist; re-run fetch-providers.")
    radiologist = in_network[0]

    # Remove any generated claim that happens to collide with the demo id.
    session.execute(delete(Claim).where(Claim.claim_id == DEMO_CLAIM_ID))
    session.execute(delete(PaRequest).where(PaRequest.pa_id == DEMO_PA_ID))

    service_date = today - dt.timedelta(days=24)
    billed = billed_from_allowed(price.avg_allowed, DEMO_CLAIM_ID)
    hero_conditions = ["M17.11"]

    session.add(
        Claim(
            claim_id=DEMO_CLAIM_ID,
            member_id=hero.member_id,
            provider_npi=radiologist.npi,
            service_date=service_date,
            icd10_codes=hero_conditions,
            hcpcs_code=knee_mri,
            place_of_service=price.place_of_service,
            billed=billed,
            allowed=price.avg_allowed,
            plan_paid=Decimal("0.00"),
            member_resp=billed,
            status="DENIED",
            denial_code="PA_MISSING",
            denial_reason=(
                "This MRI needs prior approval from your plan before it is done, "
                "and an approved request had not reached us by the service date."
            ),
            status_rule=STATUS_RULES["PA_MISSING"],
            pa_id=None,
            applied_to_deductible=Decimal("0.00"),
            applied_to_oop=Decimal("0.00"),
            is_synthetic=True,
        )
    )
    used_claim_ids.add(DEMO_CLAIM_ID)

    # A pending prior authorization the member can ask about (week-2 feature,
    # seeded now so the data is consistent from the start).
    orthopaedists = [
        provider
        for provider in providers_by_group.get("Orthopaedic Surgery", [])
        if provider.in_network
    ]
    session.add(
        PaRequest(
            pa_id=DEMO_PA_ID,
            member_id=hero.member_id,
            provider_npi=(orthopaedists or [radiologist])[0].npi,
            hcpcs_code=knee_mri,
            icd10_codes=hero_conditions,
            status="PENDING",
            submitted_at=_as_datetime(today - dt.timedelta(days=5)),
            decided_at=None,
            missing_items=[
                "Notes from a recent visit about the knee",
                "Record of at least six weeks of conservative treatment",
            ],
            notes_masked=None,
            is_synthetic=True,
        )
    )

    rule = _rule_from_row(
        benefits.get((plan.plan_id, imaging_benefit, "in_network")), imaging_benefit, "in_network"
    )
    return {
        "claim_id": DEMO_CLAIM_ID,
        "pa_id": DEMO_PA_ID,
        "service_date": service_date.isoformat(),
        "mri_allowed": str(price.avg_allowed),
        "mri_billed": str(billed),
        "imaging_rule": rule.display_text,
        "radiologist": f"{radiologist.name} (NPI {radiologist.npi})",
    }


def build_refills(
    session: Session,
    members: dict[str, Member],
    medications: dict[str, list[dict[str, Any]]],
    today: dt.date,
) -> int:
    """Refill schedules from Synthea's RxNorm-coded medication records."""
    created = 0
    for member_id, member in members.items():
        records = medications.get(member.synthea_id or "", [])
        if not records:
            continue
        # The most recent distinct drugs, newest first.
        by_code: dict[str, dict[str, Any]] = {}
        for record in sorted(records, key=lambda r: r.get("START", "")):
            by_code[record["CODE"]] = record
        latest = list(by_code.values())[-2:]
        for position, record in enumerate(latest):
            days_supply = 30
            # The hero's first refill is due in 3 days, as the demo script says.
            if member_id == HERO_MEMBER_ID and position == 0:
                next_due = today + dt.timedelta(days=3)
            else:
                offset = int(_hash_fraction("refill", member_id, record["CODE"]) * 60) - 20
                next_due = today + dt.timedelta(days=offset)
            session.add(
                Refill(
                    member_id=member_id,
                    rxcui=record["CODE"],
                    drug_name=record.get("DESCRIPTION", "").strip() or "Unnamed medication",
                    last_fill=next_due - dt.timedelta(days=days_supply),
                    days_supply=days_supply,
                    next_due=next_due,
                    is_synthetic=True,
                )
            )
            created += 1
    return created


def build_users(
    session: Session, members: dict[str, Member], providers: list[Provider]
) -> list[tuple[str, str]]:
    """Create the demo logins. Returns ``(username, role)`` pairs."""
    settings = get_settings()
    password = settings.demo_password
    if not password or len(password) < 10:
        raise SystemExit(
            "DEMO_PASSWORD in .env must be at least 10 characters; the demo logins use it."
        )
    digest = hash_password(password)

    hero = members[HERO_MEMBER_ID]
    other = members[OTHER_MEMBER_ID]
    provider = next((p for p in providers if p.in_network), providers[0])

    rows = [
        User(
            username=HERO_MEMBER_ID,
            password_hash=digest,
            role="member",
            member_id=hero.member_id,
            display_name=hero.full_name,
            is_synthetic=True,
        ),
        User(
            username=OTHER_MEMBER_ID,
            password_hash=digest,
            role="member",
            member_id=other.member_id,
            display_name=other.full_name,
            is_synthetic=True,
        ),
        User(
            username="agent",
            password_hash=digest,
            role="agent",
            display_name="Care Team Agent",
            is_synthetic=True,
        ),
        User(
            username="admin",
            password_hash=digest,
            role="admin",
            display_name="MemberAssist Admin",
            is_synthetic=True,
        ),
        User(
            username="provider",
            password_hash=digest,
            role="provider",
            provider_npi=provider.npi,
            display_name=provider.name,
            is_synthetic=True,
        ),
    ]
    session.add_all(rows)

    # The provider login may see its own patients only. Link a handful,
    # including the hero so the week-2 provider flow has something to show.
    linked = [hero.member_id, other.member_id, *sorted(members)[:6]]
    for member_id in dict.fromkeys(linked):
        session.add(
            ProviderMemberLink(provider_npi=provider.npi, member_id=member_id, is_synthetic=True)
        )
    return [(row.username, row.role) for row in rows]


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def run(session: Session, *, population: int, today: dt.date) -> dict[str, Any]:
    """Build the whole synthetic dataset. Returns a summary for the console."""
    settings = get_settings()
    plan_year = settings.plan_year

    plans = list(session.scalars(select(Plan).order_by(Plan.plan_id)))
    if not plans:
        raise SystemExit("No plans in the database. Run `manage.py fetch-plans` first.")
    providers = list(session.scalars(select(Provider).order_by(Provider.npi)))
    if not providers:
        raise SystemExit("No providers in the database. Run `manage.py fetch-providers` first.")
    price_rows = list(session.scalars(select(ProcedurePrice)))
    if not price_rows:
        raise SystemExit("No prices in the database. Run `manage.py fetch-prices` first.")

    benefits = {
        (row.plan_id, row.benefit_name, row.network_tier): row
        for row in session.scalars(select(PlanBenefit))
    }
    prices = PriceLookup({(row.hcpcs_code, row.place_of_service): row for row in price_rows})
    providers_by_group: dict[str, list[Provider]] = defaultdict(list)
    for provider in providers:
        providers_by_group[provider.specialty_group].append(provider)

    condition_map = load_condition_map()
    code_benefits = load_code_benefits()
    sample = load_synthea(population, today)

    # Clear anything a previous run created, children before parents.
    for model in (Refill, Accumulator, Claim, PaRequest, ProviderMemberLink, User, Member):
        session.execute(delete(model))
    session.flush()

    hero_plan = choose_hero_plan(plans, benefits)
    console.print(
        f"  hero plan: {hero_plan.plan_id} {hero_plan.metal_level} {hero_plan.plan_type}, "
        f"deductible {fmt_money(hero_plan.deductible_ind)}"
    )
    members = build_members(session, sample, plans, hero_plan, settings)

    plan_by_id = {plan.plan_id: plan for plan in plans}
    patient_by_synthea = {patient.synthea_id: patient for patient in sample.patients}
    used_claim_ids: set[str] = set()
    pa_counter: Counter = Counter()
    total_claims = 0
    total_pa = 0
    status_counts: dict[str, int] = defaultdict(int)
    denial_counts: dict[str, int] = defaultdict(int)

    for member_id, member in members.items():
        patient = patient_by_synthea[member.synthea_id or ""]
        plan = plan_by_id[member.plan_id]
        planned = plan_claims_for_member(
            member,
            plan,
            patient,
            sample.encounters.get(patient.synthea_id, []),
            sample.conditions.get(patient.synthea_id, set()),
            condition_map,
            code_benefits,
            prices,
            providers_by_group,
            providers,
            today,
            plan_year,
        )
        claims, pa_requests, deductible_used, oop_used = adjudicate(
            planned,
            plan,
            benefits,
            prices,
            today,
            used_claim_ids,
            pa_counter,
            DEMO_DENIED_PA_CODES if member_id == HERO_MEMBER_ID else frozenset(),
        )
        # Prior authorizations are referenced by claims, so insert them first.
        session.add_all(pa_requests)
        session.flush()
        session.add_all(claims)
        session.add(
            Accumulator(
                member_id=member_id,
                plan_year=plan_year,
                deductible_used=min(deductible_used, plan.deductible_ind),
                oop_used=min(oop_used, plan.oop_max_ind),
                is_synthetic=True,
            )
        )
        total_claims += len(claims)
        total_pa += len(pa_requests)
        for claim in claims:
            status_counts[claim.status] += 1
            if claim.denial_code:
                denial_counts[claim.denial_code] += 1
    session.flush()

    demo = install_demo_scenario(
        session,
        members[HERO_MEMBER_ID],
        hero_plan,
        benefits,
        prices,
        providers_by_group,
        today,
        used_claim_ids,
    )
    refills = build_refills(session, members, sample.medications, today)
    logins = build_users(session, members, providers)
    session.flush()

    record_source(
        session,
        key="synthea_sample",
        name="Synthea 10,000-patient pre-generated sample (COVID-19 edition)",
        url=SYNTHEA_CREDIT,
        licence_note=SYNTHEA_LICENCE,
        row_count=len(members),
        notes=(
            f"{sample.adults_available:,} living adults aged 18-90 were available; "
            f"{len(members)} selected with seed {RANDOM_SEED}. Transformations T1-T6 are "
            "documented in scripts/make_synthetic_data.py: re-homed to Dallas County TX, "
            "fictional 555-01xx phone numbers and .invalid email addresses, name digits "
            "stripped, each member's history shifted forward as a whole into plan year "
            f"{plan_year}, encounters mapped to priced HCPCS codes, billed amounts set at "
            "1.8-3.2x the allowed amount by a hash of the claim id."
        ),
    )

    return {
        "members": len(members),
        "claims": total_claims + 1,  # + the demo claim
        "pa_requests": total_pa + 1,
        "refills": refills,
        "status_counts": dict(status_counts),
        "denial_counts": dict(denial_counts),
        "logins": logins,
        "demo": demo,
        "hero_plan": hero_plan,
    }


def print_hero(session: Session, summary: dict[str, Any]) -> None:
    """Print the demo member's plan, accumulators and claims (the done-check)."""
    hero = session.get(Member, HERO_MEMBER_ID)
    if hero is None:
        raise SystemExit("Hero member was not created.")
    plan = session.get(Plan, hero.plan_id)
    accumulator = session.scalar(select(Accumulator).where(Accumulator.member_id == HERO_MEMBER_ID))
    assert plan is not None and accumulator is not None

    console.print(f"\n[bold]Demo hero member {hero.member_id}[/bold] (synthetic)")
    facts = Table(show_header=False, box=None)
    facts.add_column(style="cyan", no_wrap=True)
    facts.add_column()
    facts.add_row("Name", hero.full_name)
    facts.add_row("Date of birth", hero.dob.isoformat())
    facts.add_row("Address", f"{hero.address}, {hero.city}, {hero.state} {hero.zip_code}")
    facts.add_row("Contact", f"{hero.phone} · {hero.email}")
    facts.add_row("Plan", f"{plan.plan_id} — {plan.issuer}")
    facts.add_row("", f"{plan.name}")
    facts.add_row(
        "Design",
        f"{plan.metal_level} {plan.plan_type}, HSA: {'yes' if plan.hsa_eligible else 'no'}",
    )
    facts.add_row(
        "Deductible",
        f"{fmt_money(plan.deductible_ind)} (used {fmt_money(accumulator.deductible_used)})",
    )
    facts.add_row(
        "Out-of-pocket max",
        f"{fmt_money(plan.oop_max_ind)} (used {fmt_money(accumulator.oop_used)})",
    )
    facts.add_row("Imaging rule", summary["demo"]["imaging_rule"])
    console.print(facts)

    claims = list(
        session.scalars(
            select(Claim)
            .where(Claim.member_id == HERO_MEMBER_ID)
            .order_by(Claim.service_date.desc())
        )
    )
    table = Table(title=f"{hero.member_id} claims ({len(claims)})")
    for column in (
        "Claim",
        "Date",
        "Code",
        "Setting",
        "Billed",
        "Allowed",
        "Plan paid",
        "You owe",
        "Status",
    ):
        table.add_column(column, overflow="fold")
    for claim in claims:
        table.add_row(
            claim.claim_id,
            claim.service_date.isoformat(),
            claim.hcpcs_code,
            claim.place_of_service,
            fmt_money(claim.billed),
            fmt_money(claim.allowed),
            fmt_money(claim.plan_paid),
            fmt_money(claim.member_resp),
            f"{claim.status}{' / ' + claim.denial_code if claim.denial_code else ''}",
        )
    console.print(table)
    denied = next((c for c in claims if c.claim_id == DEMO_CLAIM_ID), None)
    if denied:
        console.print(f"  [bold]{DEMO_CLAIM_ID}[/bold] diagnosis codes: {denied.icd10_codes}")
        console.print(f"  reason given to the member: {denied.denial_reason}")
        console.print(f"  [dim]rule: {denied.status_rule}[/dim]")


def print_summary(summary: dict[str, Any]) -> None:
    """Print population-level counts."""
    table = Table(title="Synthetic population")
    table.add_column("Measure", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Members", f"{summary['members']:,}")
    table.add_row("Claims", f"{summary['claims']:,}")
    table.add_row("Prior authorizations", f"{summary['pa_requests']:,}")
    table.add_row("Refill schedules", f"{summary['refills']:,}")
    for status, count in sorted(summary["status_counts"].items()):
        table.add_row(f"  status {status}", f"{count:,}")
    for code, count in sorted(summary["denial_counts"].items()):
        table.add_row(f"  denial {code}", f"{count:,}")
    console.print(table)

    logins = Table(title="Demo logins (password from DEMO_PASSWORD in .env)")
    logins.add_column("Username", style="cyan")
    logins.add_column("Role")
    for username, role in summary["logins"]:
        logins.add_row(username, role)
    console.print(logins)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=int, default=POPULATION)
    parser.add_argument("--today", default=None, help="Override today's date (YYYY-MM-DD).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    with session_scope() as session:
        summary = run(session, population=args.population, today=today)
        print_summary(summary)
        print_hero(session, summary)
        console.print("\n[bold]Demo-script preconditions[/bold]")
        check_demo_preconditions(session, get_settings().plan_year)
        print_row_counts(session, "After make-data")
    console.print("\n[green]Synthetic data built. Every row is labelled synthetic.[/green]")


def check_demo_preconditions(session: Session, plan_year: int) -> None:
    """Fail loudly if the data cannot support the demo script.

    Every one of these is something a reviewer will watch on screen, so a
    silent miss here would waste the demo. Section 18 of the specification is
    the checklist.
    """
    problems: list[str] = []

    hero = session.get(Member, HERO_MEMBER_ID)
    other = session.get(Member, OTHER_MEMBER_ID)
    if hero is None:
        problems.append(f"hero member {HERO_MEMBER_ID} is missing")
    if other is None:
        problems.append(f"attack-test member {OTHER_MEMBER_ID} is missing")

    if hero is not None:
        plan = session.get(Plan, hero.plan_id)
        accumulator = session.scalar(
            select(Accumulator).where(
                Accumulator.member_id == HERO_MEMBER_ID, Accumulator.plan_year == plan_year
            )
        )
        claims = list(session.scalars(select(Claim).where(Claim.member_id == HERO_MEMBER_ID)))
        if accumulator is None:
            problems.append("hero has no accumulator row")
        elif accumulator.deductible_used <= 0:
            problems.append(
                "hero has used none of their deductible; the demo says part of it is used"
            )
        elif plan is not None and accumulator.deductible_used >= plan.deductible_ind:
            problems.append(
                "hero has met their whole deductible; the demo needs part of it remaining"
            )
        if len(claims) < 3:
            problems.append(f"hero has only {len(claims)} claims; the demo needs several")
        if not any(claim.claim_id == DEMO_CLAIM_ID for claim in claims):
            problems.append(f"demo claim {DEMO_CLAIM_ID} is missing")
        if not any("M17.11" in (claim.icd10_codes or []) for claim in claims):
            problems.append("no hero claim carries the knee osteoarthritis code M17.11")

    demo_claim = session.get(Claim, DEMO_CLAIM_ID)
    if demo_claim is not None:
        if demo_claim.denial_code != "PA_MISSING":
            problems.append(f"{DEMO_CLAIM_ID} is not denied PA_MISSING")
        if demo_claim.hcpcs_code != "73721":
            problems.append(f"{DEMO_CLAIM_ID} is not the knee MRI (73721)")

    pa = session.get(PaRequest, DEMO_PA_ID)
    if pa is None or pa.status != "PENDING":
        problems.append(f"{DEMO_PA_ID} is missing or not pending")

    refill = session.scalar(
        select(Refill).where(Refill.member_id == HERO_MEMBER_ID).order_by(Refill.next_due)
    )
    if refill is None:
        problems.append("hero has no refill schedule")

    # Every denial reason the product explains must actually occur somewhere.
    for code in ("PA_MISSING", "OUT_OF_NETWORK", "NOT_COVERED"):
        if not session.scalar(select(Claim.claim_id).where(Claim.denial_code == code).limit(1)):
            problems.append(f"no claim anywhere has denial code {code}")

    if not session.scalar(select(Claim.claim_id).where(Claim.status == "PAID").limit(1)):
        problems.append("no claim anywhere was paid")

    if problems:
        for problem in problems:
            console.print(f"  [red]x[/red] {problem}")
        raise SystemExit("The dataset cannot run the demo script. Fix the generator.")
    console.print("  [green]ok[/green] every demo-script precondition holds")


if __name__ == "__main__":
    main()
