"""Table-driven tests for the cost-sharing arithmetic.

These are the figures a member could check against a statement, so every case
in the specification is asserted exactly rather than approximately.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.cost_sharing import (
    Accumulators,
    BenefitRule,
    settle,
    settle_out_of_network,
)


def rule(
    *,
    copay: str | None = None,
    coinsurance: str | None = None,
    after_deductible: bool = False,
    is_covered: bool = True,
    tier: str = "in_network",
) -> BenefitRule:
    """Build a benefit rule from readable strings."""
    return BenefitRule(
        benefit_name="Imaging (CT/PET Scans, MRIs)",
        network_tier=tier,
        copay=Decimal(copay) if copay is not None else None,
        coinsurance=Decimal(coinsurance) if coinsurance is not None else None,
        after_deductible=after_deductible,
        is_covered=is_covered,
        display_text="test rule",
    )


def acc(deductible_left: str, oop_left: str = "100000") -> Accumulators:
    return Accumulators(Decimal(deductible_left), Decimal(oop_left))


# ---------------------------------------------------------------------------
# the specification's worked example
# ---------------------------------------------------------------------------
def test_specification_worked_example_is_960() -> None:
    """$900 deductible left, 20% coinsurance after deductible, $1,200 -> $960."""
    result = settle(Decimal("1200"), rule(coinsurance="0.20", after_deductible=True), acc("900"))
    assert result.member_pays == Decimal("960.00")
    assert result.applied_to_deductible == Decimal("900.00")
    assert result.plan_pays == Decimal("240.00")
    assert result.rule_kind == "coinsurance_after_deductible"
    # The breakdown must explain both halves of the figure.
    joined = " ".join(result.steps)
    assert "$900.00" in joined
    assert "20%" in joined
    assert "$60.00" in joined


# ---------------------------------------------------------------------------
# each benefit type
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("price", "benefit", "accumulators", "expected", "expected_kind"),
    [
        # Copay, deductible does not apply: the copay, or the price if cheaper.
        ("204.41", rule(copay="40"), acc("1000"), "40.00", "copay"),
        ("25.00", rule(copay="40"), acc("1000"), "25.00", "copay"),
        # Copay after deductible, price fully inside the remaining deductible.
        (
            "100.00",
            rule(copay="40", after_deductible=True),
            acc("500"),
            "100.00",
            "copay_after_deductible",
        ),
        # Copay after deductible, deductible partly covers the price.
        (
            "300.00",
            rule(copay="40", after_deductible=True),
            acc("100"),
            "140.00",
            "copay_after_deductible",
        ),
        # Copay after deductible, deductible already met.
        (
            "300.00",
            rule(copay="40", after_deductible=True),
            acc("0"),
            "40.00",
            "copay_after_deductible",
        ),
        # Coinsurance, deductible does not apply.
        ("204.41", rule(coinsurance="0.30"), acc("1000"), "61.32", "coinsurance"),
        # Coinsurance after deductible, deductible already met.
        (
            "204.41",
            rule(coinsurance="0.30", after_deductible=True),
            acc("0"),
            "61.32",
            "coinsurance_after_deductible",
        ),
        # Coinsurance after deductible, price below the remaining deductible.
        (
            "204.41",
            rule(coinsurance="0.30", after_deductible=True),
            acc("1000"),
            "204.41",
            "coinsurance_after_deductible",
        ),
        # Zero coinsurance after the deductible: the member pays the deductible only.
        (
            "500.00",
            rule(coinsurance="0.00", after_deductible=True),
            acc("200"),
            "200.00",
            "coinsurance_after_deductible",
        ),
        # No charge.
        ("500.00", rule(copay="0", coinsurance="0"), acc("1000"), "0.00", "no_charge"),
        # Not covered: the member owes everything.
        (
            "500.00",
            rule(coinsurance="1.00", is_covered=False, tier="out_of_network"),
            acc("1000"),
            "500.00",
            "not_covered",
        ),
        # Out of network at 100% coinsurance but still "covered" in the filing.
        (
            "500.00",
            rule(coinsurance="1.00", after_deductible=True),
            acc("0"),
            "500.00",
            "coinsurance_after_deductible",
        ),
    ],
)
def test_benefit_types(
    price: str, benefit: BenefitRule, accumulators: Accumulators, expected: str, expected_kind: str
) -> None:
    result = settle(Decimal(price), benefit, accumulators)
    assert result.member_pays == Decimal(expected)
    assert result.rule_kind == expected_kind


# ---------------------------------------------------------------------------
# the out-of-pocket maximum
# ---------------------------------------------------------------------------
def test_out_of_pocket_maximum_caps_the_member_share() -> None:
    result = settle(
        Decimal("5000"),
        rule(coinsurance="0.40", after_deductible=True),
        acc("4000", oop_left="250"),
    )
    assert result.member_pays == Decimal("250.00")
    assert result.oop_capped is True
    assert result.plan_pays == Decimal("4750.00")
    assert "capped" in " ".join(result.steps)


def test_met_out_of_pocket_maximum_means_the_member_pays_nothing() -> None:
    result = settle(
        Decimal("1200"), rule(coinsurance="0.20", after_deductible=True), acc("900", oop_left="0")
    )
    assert result.member_pays == Decimal("0.00")
    assert result.plan_pays == Decimal("1200.00")
    assert result.oop_capped is True


def test_deductible_applied_never_exceeds_the_capped_member_share() -> None:
    """A capped share must not claim more deductible than the member paid."""
    result = settle(
        Decimal("3000"),
        rule(coinsurance="0.20", after_deductible=True),
        acc("3000", oop_left="500"),
    )
    assert result.member_pays == Decimal("500.00")
    assert result.applied_to_deductible <= result.member_pays


# ---------------------------------------------------------------------------
# edge cases and invariants
# ---------------------------------------------------------------------------
def test_zero_price_costs_nothing() -> None:
    result = settle(Decimal("0"), rule(coinsurance="0.30", after_deductible=True), acc("500"))
    assert result.member_pays == Decimal("0.00")
    assert result.plan_pays == Decimal("0.00")


def test_negative_price_is_treated_as_zero() -> None:
    result = settle(Decimal("-50"), rule(copay="40"), acc("500"))
    assert result.member_pays == Decimal("0.00")


def test_unpriced_tier_refuses_to_guess() -> None:
    """A covered benefit with no stated cost sharing must not invent a figure."""
    result = settle(Decimal("204.41"), rule(), acc("500"))
    assert result.rule_kind == "unpriced"
    assert result.member_pays == Decimal("0.00")
    assert "cannot work out" in " ".join(result.steps)


@pytest.mark.parametrize("price", ["0", "9.34", "204.41", "1159.35", "25000"])
@pytest.mark.parametrize("deductible_left", ["0", "900", "4000"])
def test_member_and_plan_shares_always_sum_to_the_price(price: str, deductible_left: str) -> None:
    """The arithmetic must never lose or invent a cent."""
    result = settle(
        Decimal(price), rule(coinsurance="0.30", after_deductible=True), acc(deductible_left)
    )
    assert result.member_pays + result.plan_pays == Decimal(price).quantize(Decimal("0.01"))


def test_rounding_is_half_up_like_a_statement() -> None:
    """0.5 cents rounds up, as billing does, not to even as Python defaults."""
    # 15% of 123.45 = 18.5175 -> 18.52
    result = settle(Decimal("123.45"), rule(coinsurance="0.15"), acc("0"))
    assert result.member_pays == Decimal("18.52")


# ---------------------------------------------------------------------------
# zero-rate designs that really appear in the CMS filings
# ---------------------------------------------------------------------------
def test_zero_percent_coinsurance_after_deductible_still_charges_the_deductible() -> None:
    """ "0.00% Coinsurance after deductible" appears 1,369 times in the TX filings.

    It means: pay your deductible, then the plan covers the rest. It must not
    be read as "free", and it must not fall through as unpriced.
    """
    result = settle(Decimal("500"), rule(coinsurance="0.00", after_deductible=True), acc("200"))
    assert result.rule_kind == "coinsurance_after_deductible"
    assert result.member_pays == Decimal("200.00")
    assert result.applied_to_deductible == Decimal("200.00")
    assert result.plan_pays == Decimal("300.00")


def test_no_charge_after_deductible_charges_the_deductible_only() -> None:
    """ "No Charge after deductible" appears 662 times; same shape, copay form."""
    result = settle(Decimal("500"), rule(copay="0", after_deductible=True), acc("200"))
    assert result.rule_kind == "copay_after_deductible"
    assert result.member_pays == Decimal("200.00")
    assert result.plan_pays == Decimal("300.00")


def test_plain_no_charge_is_free() -> None:
    """A zero cost share with no deductible really is free."""
    result = settle(Decimal("500"), rule(copay="0", coinsurance="0"), acc("4000"))
    assert result.rule_kind == "no_charge"
    assert result.member_pays == Decimal("0.00")


# ---------------------------------------------------------------------------
# out of network, where balance billing applies
# ---------------------------------------------------------------------------
def test_out_of_network_applies_cost_sharing_to_the_allowed_amount() -> None:
    """The plan's share is calculated on what it allows, not on the charge.

    Allowed $200, charged $600, 50% coinsurance after a met deductible: the
    member's cost share is $100, and the provider bills the remaining $400
    balance on top, so the member owes $500 and the plan pays $100.
    """
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("600"),
        rule(coinsurance="0.50", after_deductible=True, tier="out_of_network"),
        acc("0"),
    )
    assert result.plan_pays == Decimal("100.00")
    assert result.balance_billed == Decimal("400.00")
    assert result.member_pays == Decimal("500.00")
    # The plan and the member still account for the whole charge.
    assert result.member_pays + result.plan_pays == Decimal("600.00")


def test_balance_billing_counts_toward_neither_accumulator() -> None:
    """The plan never agreed to the balance, so it does not credit it."""
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("600"),
        rule(coinsurance="0.50", after_deductible=True, tier="out_of_network"),
        acc("0"),
    )
    assert result.counts_toward_oop == Decimal("100.00")
    assert result.counts_toward_oop < result.member_pays
    assert result.applied_to_deductible == Decimal("0.00")


def test_out_of_network_deductible_applies_to_the_allowed_amount_only() -> None:
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("600"),
        rule(coinsurance="0.50", after_deductible=True, tier="out_of_network"),
        acc("500"),
    )
    assert result.applied_to_deductible == Decimal("200.00")
    assert result.member_pays == Decimal("600.00")  # all of it, plus the balance
    assert result.plan_pays == Decimal("0.00")
    assert result.counts_toward_oop == Decimal("200.00")


def test_out_of_network_warns_about_the_balance_bill() -> None:
    """A member must be told the balance exists; it is the expensive surprise."""
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("600"),
        rule(coinsurance="0.50", after_deductible=True, tier="out_of_network"),
        acc("0"),
    )
    joined = " ".join(result.steps)
    assert "outside your plan's network" in joined
    assert "$400.00" in joined
    assert "does not count toward your deductible" in joined


def test_out_of_network_not_covered_means_the_whole_charge() -> None:
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("600"),
        rule(coinsurance="1.00", is_covered=False, tier="out_of_network"),
        acc("0"),
    )
    assert result.member_pays == Decimal("600.00")
    assert result.plan_pays == Decimal("0.00")
    assert result.counts_toward_oop == Decimal("0.00")


def test_a_charge_below_the_allowed_amount_creates_no_balance() -> None:
    result = settle_out_of_network(
        Decimal("200"),
        Decimal("150"),
        rule(coinsurance="0.50", after_deductible=True, tier="out_of_network"),
        acc("0"),
    )
    assert result.balance_billed == Decimal("0.00")
    assert result.member_pays == Decimal("100.00")
