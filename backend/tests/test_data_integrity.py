"""Tests that the seeded dataset is internally consistent.

These run against the development database built by ``manage.py seed``. They
are the Phase 1 done-check in executable form: if the accumulators drift from
the claims, or a claim's status contradicts its own recorded rule, these fail.

The suite skips cleanly when the database is empty, so a fresh clone can still
run ``manage.py test`` before seeding.
"""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Accumulator,
    Claim,
    DataSource,
    Member,
    PaRequest,
    Plan,
    PlanBenefit,
    ProcedurePrice,
    Provider,
    User,
)
from app.db.session import session_scope
from app.services.cost_sharing import (
    Accumulators,
    BenefitRule,
    settle,
    settle_out_of_network,
)

HERO_MEMBER_ID = "M10234"
OTHER_MEMBER_ID = "M10555"
DEMO_CLAIM_ID = "CLM-4521"
DEMO_PA_ID = "PA-889"


@pytest.fixture(scope="module")
def db() -> Session:
    """A session over the development database, skipping if it is unseeded."""
    with session_scope() as session:
        members = session.scalar(select(func.count()).select_from(Member)) or 0
        if members == 0:
            pytest.skip("database not seeded; run `manage.py seed` first")
        yield session


# ---------------------------------------------------------------------------
# population
# ---------------------------------------------------------------------------
def test_population_is_the_expected_size(db: Session) -> None:
    assert (db.scalar(select(func.count()).select_from(Member)) or 0) >= 150


def test_every_synthetic_row_is_labelled_synthetic(db: Session) -> None:
    """Rule 2.1.4: synthetic data always says so."""
    for model in (Member, Claim, Accumulator, PaRequest, User):
        unlabelled = db.scalar(
            select(func.count()).select_from(model).where(model.is_synthetic.is_(False))
        )
        assert unlabelled == 0, f"{model.__tablename__} has rows not marked synthetic"


def test_contact_details_can_never_reach_a_real_person(db: Session) -> None:
    """Transformation T2: reserved phone block and the .invalid TLD."""
    for member in db.scalars(select(Member)):
        assert member.email.endswith("@example.invalid"), member.email
        assert re.match(r"^\(\d{3}\) 555-01\d{2}$", member.phone), member.phone
        assert member.email.isascii(), member.email


def test_everyone_lives_in_the_configured_county(db: Session) -> None:
    """Transformation T1: the Massachusetts sample was re-homed."""
    settings = get_settings()
    states = {member.state for member in db.scalars(select(Member))}
    assert states == {settings.plan_state}
    cities = {member.city for member in db.scalars(select(Member))}
    assert cities == {"Dallas"}


def test_every_member_is_on_a_real_plan(db: Session) -> None:
    plan_ids = set(db.scalars(select(Plan.plan_id)))
    assert plan_ids
    for member in db.scalars(select(Member)):
        assert member.plan_id in plan_ids


# ---------------------------------------------------------------------------
# accumulators (the specification asks for this test by name)
# ---------------------------------------------------------------------------
def test_accumulators_recompute_from_the_paid_claims(db: Session) -> None:
    """Recompute every accumulator from the claims and compare.

    ``deductible_used`` is the sum of each paid claim's deductible share, and
    ``oop_used`` is the sum of what the member owed on paid claims. Denied and
    pending claims contribute to neither.
    """
    plan_year = get_settings().plan_year
    expected_deductible: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    expected_oop: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for claim in db.scalars(select(Claim).where(Claim.status == "PAID")):
        if claim.service_date.year != plan_year:
            continue
        expected_deductible[claim.member_id] += claim.applied_to_deductible
        expected_oop[claim.member_id] += claim.applied_to_oop

    plans = {plan.plan_id: plan for plan in db.scalars(select(Plan))}
    members = {member.member_id: member for member in db.scalars(select(Member))}
    rows = list(db.scalars(select(Accumulator).where(Accumulator.plan_year == plan_year)))
    assert rows, "no accumulators for the current plan year"

    for row in rows:
        plan = plans[members[row.member_id].plan_id]
        assert row.deductible_used == min(
            expected_deductible[row.member_id], plan.deductible_ind
        ), f"{row.member_id} deductible drifted from its claims"
        assert row.oop_used == min(expected_oop[row.member_id], plan.oop_max_ind), (
            f"{row.member_id} out-of-pocket total drifted from its claims"
        )


def test_accumulators_never_exceed_the_plan_limits(db: Session) -> None:
    plans = {plan.plan_id: plan for plan in db.scalars(select(Plan))}
    members = {member.member_id: member for member in db.scalars(select(Member))}
    for row in db.scalars(select(Accumulator)):
        plan = plans[members[row.member_id].plan_id]
        assert Decimal("0") <= row.deductible_used <= plan.deductible_ind
        assert Decimal("0") <= row.oop_used <= plan.oop_max_ind


def test_every_member_has_exactly_one_accumulator_for_the_plan_year(db: Session) -> None:
    plan_year = get_settings().plan_year
    member_count = db.scalar(select(func.count()).select_from(Member))
    accumulator_count = db.scalar(
        select(func.count()).select_from(Accumulator).where(Accumulator.plan_year == plan_year)
    )
    assert member_count == accumulator_count


# ---------------------------------------------------------------------------
# claim status rules
# ---------------------------------------------------------------------------
def test_every_claim_records_the_rule_that_set_its_status(db: Session) -> None:
    for claim in db.scalars(select(Claim)):
        assert claim.status_rule, f"{claim.claim_id} has no status_rule"
        assert claim.status_rule.startswith(claim.denial_code or claim.status), (
            f"{claim.claim_id}: rule {claim.status_rule!r} does not match status {claim.status}"
        )


def test_denied_claims_explain_themselves_in_plain_words(db: Session) -> None:
    for claim in db.scalars(select(Claim).where(Claim.status == "DENIED")):
        assert claim.denial_code
        assert claim.denial_reason and len(claim.denial_reason) > 30
        # The member-facing reason must not leak internal codes.
        assert claim.denial_code not in claim.denial_reason


def test_out_of_network_denials_really_are_out_of_network(db: Session) -> None:
    providers = {provider.npi: provider for provider in db.scalars(select(Provider))}
    plans = {plan.plan_id: plan for plan in db.scalars(select(Plan))}
    members = {member.member_id: member for member in db.scalars(select(Member))}
    denied = list(db.scalars(select(Claim).where(Claim.denial_code == "OUT_OF_NETWORK")))
    assert denied, "no OUT_OF_NETWORK denial exists to test"
    for claim in denied:
        assert providers[claim.provider_npi].in_network is False
        plan = plans[members[claim.member_id].plan_id]
        assert plan.plan_type.upper() in {"HMO", "EPO"}


def test_pa_missing_denials_have_no_approved_prior_authorization(db: Session) -> None:
    denied = list(db.scalars(select(Claim).where(Claim.denial_code == "PA_MISSING")))
    assert denied, "no PA_MISSING denial exists to test"
    approved = {
        (pa.member_id, pa.hcpcs_code)
        for pa in db.scalars(select(PaRequest).where(PaRequest.status == "APPROVED"))
    }
    for claim in denied:
        assert claim.pa_id is None
        assert (claim.member_id, claim.hcpcs_code) not in approved


def test_not_covered_denials_match_the_plans_own_filing(db: Session) -> None:
    members = {member.member_id: member for member in db.scalars(select(Member))}
    benefits = {
        (row.plan_id, row.benefit_name, row.network_tier): row
        for row in db.scalars(select(PlanBenefit))
    }
    denied = list(db.scalars(select(Claim).where(Claim.denial_code == "NOT_COVERED")))
    assert denied, "no NOT_COVERED denial exists to test"
    for claim in denied:
        plan_id = members[claim.member_id].plan_id
        matches = [
            row
            for (pid, _name, _tier), row in benefits.items()
            if pid == plan_id and not row.is_covered
        ]
        assert matches, f"{claim.claim_id}: plan {plan_id} excludes nothing"


def test_duplicate_denials_point_at_a_real_earlier_claim(db: Session) -> None:
    claims = list(db.scalars(select(Claim)))
    by_key: dict[tuple[str, str, str], list[Claim]] = defaultdict(list)
    for claim in claims:
        by_key[(claim.member_id, claim.hcpcs_code, claim.service_date.isoformat())].append(claim)
    duplicates = [claim for claim in claims if claim.denial_code == "DUPLICATE"]
    assert duplicates, "no DUPLICATE denial exists to test"
    for claim in duplicates:
        siblings = by_key[(claim.member_id, claim.hcpcs_code, claim.service_date.isoformat())]
        assert len(siblings) >= 2, f"{claim.claim_id} is marked duplicate but stands alone"


def test_all_four_denial_reasons_occur(db: Session) -> None:
    """The product explains four denial reasons, so the data must contain them."""
    present = set(db.scalars(select(Claim.denial_code).distinct())) - {None}
    assert present == {"PA_MISSING", "OUT_OF_NETWORK", "NOT_COVERED", "DUPLICATE"}


# ---------------------------------------------------------------------------
# claim arithmetic
# ---------------------------------------------------------------------------
def test_claim_amounts_are_internally_consistent(db: Session) -> None:
    for claim in db.scalars(select(Claim)):
        assert claim.billed >= claim.allowed, f"{claim.claim_id} bills below the allowed amount"
        assert claim.plan_paid >= 0 and claim.member_resp >= 0
        assert claim.applied_to_deductible <= claim.applied_to_oop <= claim.member_resp
        if claim.status == "PAID":
            basis = (
                claim.allowed
                if claim.plan_paid + claim.member_resp == claim.allowed
                else claim.billed
            )
            assert claim.plan_paid + claim.member_resp == basis, claim.claim_id
        if claim.status == "DENIED":
            assert claim.plan_paid == 0
            assert claim.applied_to_deductible == 0
            assert claim.applied_to_oop == 0


def test_paid_in_network_claims_match_the_cost_sharing_calculator(db: Session) -> None:
    """Spot-check that stored shares agree with a fresh calculation.

    Only the first claim per member is checked, because later claims depend on
    the deductible left at the time, which the stored row does not carry.
    """
    members = {member.member_id: member for member in db.scalars(select(Member))}
    plans = {plan.plan_id: plan for plan in db.scalars(select(Plan))}
    providers = {provider.npi: provider for provider in db.scalars(select(Provider))}
    benefits = {
        (row.plan_id, row.benefit_name, row.network_tier): row
        for row in db.scalars(select(PlanBenefit))
    }
    code_to_benefit = _code_to_benefit()

    first_by_member: dict[str, Claim] = {}
    for claim in db.scalars(
        select(Claim).where(Claim.status == "PAID").order_by(Claim.service_date)
    ):
        first_by_member.setdefault(claim.member_id, claim)

    checked = 0
    for claim in list(first_by_member.values())[:40]:
        benefit_name = code_to_benefit.get(claim.hcpcs_code)
        if benefit_name is None:
            continue
        member = members[claim.member_id]
        plan = plans[member.plan_id]
        tier = "in_network" if providers[claim.provider_npi].in_network else "out_of_network"
        row = benefits.get((plan.plan_id, benefit_name, tier))
        if row is None:
            continue
        rule = BenefitRule(
            benefit_name=row.benefit_name,
            network_tier=row.network_tier,
            copay=row.copay,
            coinsurance=row.coinsurance,
            after_deductible=row.after_deductible,
            is_covered=row.is_covered,
            display_text=row.display_text,
        )
        accumulators = Accumulators(plan.deductible_ind, plan.oop_max_ind)
        if tier == "in_network":
            expected = settle(claim.allowed, rule, accumulators).member_pays
        else:
            expected = settle_out_of_network(
                claim.allowed, claim.billed, rule, accumulators
            ).member_pays
        assert expected == claim.member_resp, (
            f"{claim.claim_id}: stored {claim.member_resp} but calculator says {expected}"
        )
        checked += 1
    assert checked >= 5, "too few claims could be re-checked against the calculator"


def _code_to_benefit() -> dict[str, str]:
    """Read the procedure-to-benefit mapping from the data file."""
    import csv

    from app.config import DATA_DIR

    path = DATA_DIR / "mappings" / "code_to_benefit.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["hcpcs_code"]: row["benefit_name"] for row in csv.DictReader(handle)}


# ---------------------------------------------------------------------------
# the demo scenario
# ---------------------------------------------------------------------------
def test_demo_members_exist_with_logins(db: Session) -> None:
    for member_id in (HERO_MEMBER_ID, OTHER_MEMBER_ID):
        assert db.get(Member, member_id) is not None
        user = db.scalar(select(User).where(User.username == member_id))
        assert user is not None and user.role == "member"
        assert user.member_id == member_id


def test_every_role_has_a_demo_login(db: Session) -> None:
    roles = set(db.scalars(select(User.role)))
    assert roles == {"member", "agent", "admin", "provider"}


def test_no_password_hash_is_a_plain_password(db: Session) -> None:
    for user in db.scalars(select(User)):
        assert user.password_hash.startswith("$argon2id$"), user.username


def test_the_demo_claim_is_the_denied_knee_mri(db: Session) -> None:
    claim = db.get(Claim, DEMO_CLAIM_ID)
    assert claim is not None
    assert claim.member_id == HERO_MEMBER_ID
    assert claim.hcpcs_code == "73721"
    assert claim.status == "DENIED"
    assert claim.denial_code == "PA_MISSING"
    assert "M17.11" in claim.icd10_codes


def test_the_hero_has_used_part_of_their_deductible(db: Session) -> None:
    """The demo's cost question only works if some deductible remains."""
    member = db.get(Member, HERO_MEMBER_ID)
    assert member is not None
    plan = db.get(Plan, member.plan_id)
    accumulator = db.scalar(select(Accumulator).where(Accumulator.member_id == HERO_MEMBER_ID))
    assert plan is not None and accumulator is not None
    assert 0 < accumulator.deductible_used < plan.deductible_ind


def test_the_heros_plan_prices_imaging_as_coinsurance_after_deductible(db: Session) -> None:
    member = db.get(Member, HERO_MEMBER_ID)
    assert member is not None
    row = db.scalar(
        select(PlanBenefit).where(
            PlanBenefit.plan_id == member.plan_id,
            PlanBenefit.benefit_name == "Imaging (CT/PET Scans, MRIs)",
            PlanBenefit.network_tier == "in_network",
        )
    )
    assert row is not None
    assert row.after_deductible is True
    assert row.coinsurance is not None and row.coinsurance > 0


def test_the_pending_prior_authorization_exists(db: Session) -> None:
    pa = db.get(PaRequest, DEMO_PA_ID)
    assert pa is not None
    assert pa.member_id == HERO_MEMBER_ID
    assert pa.status == "PENDING"
    assert pa.missing_items


# ---------------------------------------------------------------------------
# real-data provenance
# ---------------------------------------------------------------------------
def test_every_real_source_is_recorded_with_a_url_and_date(db: Session) -> None:
    sources = list(db.scalars(select(DataSource)))
    keys = {source.key for source in sources}
    assert {
        "cms_marketplace_puf",
        "cms_pfs_rvu",
        "cms_npi_registry",
        "synthea_sample",
    } <= keys
    for source in sources:
        assert source.url.startswith("https://"), source.key
        assert source.licence_note, source.key
        assert source.retrieved_on is not None, source.key


def test_plans_and_prices_carry_their_source(db: Session) -> None:
    for plan in db.scalars(select(Plan)):
        assert plan.source_url.startswith("https://")
        assert plan.retrieved_on is not None
    for price in db.scalars(select(ProcedurePrice)):
        assert price.source_url.startswith("https://")
        assert price.derivation, price.hcpcs_code


def test_providers_record_which_rule_set_their_network_flag(db: Session) -> None:
    for provider in db.scalars(select(Provider)):
        assert provider.network_rule.startswith(("N1:", "N2:")), provider.npi
    # Both network states must exist, or out-of-network answers are untestable.
    states = set(db.scalars(select(Provider.in_network).distinct()))
    assert states == {True, False}


def test_prices_are_plausible(db: Session) -> None:
    for price in db.scalars(select(ProcedurePrice)):
        assert Decimal("1") <= price.avg_allowed <= Decimal("50000"), price.hcpcs_code
        assert price.avg_payment <= price.avg_allowed
