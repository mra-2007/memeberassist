"""Cost-sharing arithmetic. Pure Python, no model involved.

This is the one place that works out what a member pays. The LLM explains the
result; it never calculates it. The claim generator uses it to settle synthetic
claims and the ``estimate_cost`` tool uses it to quote an estimate, so a claim
and an estimate for the same service can never disagree.

The formula, for a price ``P`` and a remaining deductible ``ded_left``:

=============================================  ==========================================
Benefit type                                   Member pays
=============================================  ==========================================
Copay, not subject to deductible               ``min(copay, P)``
Copay after deductible                         ``d = min(P, ded_left)``; ``d + min(copay, P - d)`` when ``P > d``
Coinsurance, not subject to deductible         ``coinsurance * P``
Coinsurance after deductible                   ``d = min(P, ded_left)``; ``d + coinsurance * (P - d)``
No charge                                      ``0``
=============================================  ==========================================

Then the out-of-pocket maximum caps it: ``pay = min(pay, oop_left)``.

Worked example from the specification: $900 deductible left, 20% coinsurance
after deductible, a $1,200 price. ``d = 900``, then 20% of the remaining $300
is $60, so the member pays **$960**. ``tests/test_cost_sharing.py`` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal) -> Decimal:
    """Round to cents the way a statement does (half up, not banker's)."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class BenefitRule:
    """The plan's cost sharing for one benefit in one network tier."""

    benefit_name: str
    network_tier: str
    copay: Decimal | None
    #: A fraction: 0.20 means 20%.
    coinsurance: Decimal | None
    after_deductible: bool
    is_covered: bool
    #: The plan's own wording, for display and citation.
    display_text: str

    @property
    def kind(self) -> str:
        """Name the benefit type, for the breakdown and for tests.

        A zero rate is still a rate. The real filings contain both
        ``0.00% Coinsurance after deductible`` and ``No Charge after
        deductible``, which mean "pay your deductible, then nothing" -- not
        "free". Only a zero cost share with no deductible is genuinely free.
        """
        if not self.is_covered:
            return "not_covered"
        zero_copay = self.copay == ZERO
        zero_coinsurance = self.coinsurance is None or self.coinsurance == ZERO
        if not self.after_deductible and zero_copay and zero_coinsurance:
            return "no_charge"
        if self.coinsurance is not None:
            return "coinsurance_after_deductible" if self.after_deductible else "coinsurance"
        if self.copay is not None:
            return "copay_after_deductible" if self.after_deductible else "copay"
        return "unpriced"


@dataclass(frozen=True, slots=True)
class Accumulators:
    """What the member has left to spend this plan year."""

    deductible_left: Decimal
    oop_left: Decimal


@dataclass(slots=True)
class Settlement:
    """What the member pays for one service, and how that was worked out."""

    member_pays: Decimal
    applied_to_deductible: Decimal
    plan_pays: Decimal
    price_used: Decimal
    rule_kind: str
    #: One line per arithmetic step, in the order performed.
    steps: list[str] = field(default_factory=list)
    #: True when the out-of-pocket maximum reduced the amount owed.
    oop_capped: bool = False
    #: True when the plan does not cover this service at all.
    not_covered: bool = False


def settle(
    price: Decimal,
    rule: BenefitRule,
    accumulators: Accumulators,
) -> Settlement:
    """Work out what a member pays for one service at one price.

    Args:
        price: The allowed amount (in network) or the billed charge (out of
            network) that cost sharing applies to.
        rule: The plan's cost sharing for this benefit and network tier.
        accumulators: Remaining deductible and out-of-pocket maximum.

    Returns:
        A :class:`Settlement` whose ``steps`` explain every figure, so the
        answer a member sees can be checked line by line.
    """
    price = money(max(price, ZERO))
    ded_left = money(max(accumulators.deductible_left, ZERO))
    oop_left = money(max(accumulators.oop_left, ZERO))
    steps: list[str] = []
    kind = rule.kind

    if kind == "not_covered":
        # Nothing is covered, so the member owes the whole charge and none of
        # it counts toward the deductible or the out-of-pocket maximum.
        return Settlement(
            member_pays=price,
            applied_to_deductible=ZERO,
            plan_pays=ZERO,
            price_used=price,
            rule_kind=kind,
            steps=[
                f"This plan does not cover {rule.benefit_name.lower()} "
                f"{rule.network_tier.replace('_', ' ')}, so the full {_fmt(price)} is yours."
            ],
            not_covered=True,
        )

    if kind == "no_charge":
        return Settlement(
            member_pays=ZERO,
            applied_to_deductible=ZERO,
            plan_pays=price,
            price_used=price,
            rule_kind=kind,
            steps=[f"Your plan covers this in full, so you pay $0 of the {_fmt(price)}."],
        )

    if kind == "unpriced":
        # The filing covers the benefit but states no copay or coinsurance for
        # this tier. Guessing a number would be worse than saying so.
        return Settlement(
            member_pays=ZERO,
            applied_to_deductible=ZERO,
            plan_pays=ZERO,
            price_used=price,
            rule_kind=kind,
            steps=[
                "Your plan covers this but its filing does not state a copay or "
                "coinsurance for this network tier, so I cannot work out your share."
            ],
        )

    applied_to_deductible = ZERO
    if rule.after_deductible and ded_left > ZERO:
        applied_to_deductible = money(min(price, ded_left))
        steps.append(
            f"This service is subject to your deductible, and you have "
            f"{_fmt(ded_left)} of it left, so {_fmt(applied_to_deductible)} "
            "of the price goes to your deductible."
        )
    elif rule.after_deductible:
        steps.append("You have already met your deductible for the year.")

    remainder = money(price - applied_to_deductible)
    member_pays = applied_to_deductible

    if kind.startswith("coinsurance"):
        coinsurance = rule.coinsurance or ZERO
        share = money(coinsurance * remainder)
        member_pays = money(member_pays + share)
        steps.append(
            f"Your plan then pays all but {_percent(coinsurance)} of the remaining "
            f"{_fmt(remainder)}, so your coinsurance is {_fmt(share)}."
        )
    else:  # copay, with or without the deductible first
        copay = rule.copay or ZERO
        if remainder > ZERO:
            share = money(min(copay, remainder))
            member_pays = money(member_pays + share)
            steps.append(f"A {_fmt(copay)} copay applies, so you pay {_fmt(share)} more.")
        else:
            steps.append(
                f"The whole price went to your deductible, so the {_fmt(copay)} "
                "copay does not add anything."
            )

    oop_capped = False
    if member_pays > oop_left:
        steps.append(
            f"Your out-of-pocket maximum leaves {_fmt(oop_left)} before your plan "
            f"pays everything, so your share is capped at {_fmt(oop_left)}."
        )
        member_pays = oop_left
        applied_to_deductible = money(min(applied_to_deductible, member_pays))
        oop_capped = True

    return Settlement(
        member_pays=member_pays,
        applied_to_deductible=applied_to_deductible,
        plan_pays=money(price - member_pays),
        price_used=price,
        rule_kind=kind,
        steps=steps,
        oop_capped=oop_capped,
    )


def _fmt(value: Decimal) -> str:
    """Money for a member-facing sentence."""
    return f"${value:,.2f}"


def _percent(fraction: Decimal) -> str:
    """A stored coinsurance fraction as a percentage."""
    scaled = fraction * Decimal("100")
    text = f"{scaled.normalize():f}"
    return f"{text.rstrip('.')}%"


@dataclass(slots=True)
class OutOfNetworkSettlement:
    """What a member pays out of network, where balance billing applies.

    Out of network there is no negotiated rate. The plan still works out an
    allowed amount and applies the out-of-network cost sharing to *that*; the
    provider may then bill the member for the difference between their charge
    and that allowed amount. That balance is the member's, but it does **not**
    count toward the deductible or the out-of-pocket maximum, because the plan
    never agreed to it.

    Applying coinsurance to the full charge instead would overstate what the
    plan pays and understate what the member owes, which is the opposite of a
    useful warning.
    """

    #: Everything the member owes: their cost share plus the balance bill.
    member_pays: Decimal
    #: The plan's share, calculated on the allowed amount only.
    plan_pays: Decimal
    #: The part that counts toward the deductible.
    applied_to_deductible: Decimal
    #: The part that counts toward the out-of-pocket maximum.
    counts_toward_oop: Decimal
    #: Charge above the allowed amount, billed to the member by the provider.
    balance_billed: Decimal
    steps: list[str] = field(default_factory=list)


def settle_out_of_network(
    allowed: Decimal,
    billed: Decimal,
    rule: BenefitRule,
    accumulators: Accumulators,
) -> OutOfNetworkSettlement:
    """Settle one out-of-network service, including the balance bill.

    Args:
        allowed: The amount the plan allows for this service.
        billed: What the provider charged.
        rule: The plan's out-of-network cost sharing for this benefit.
        accumulators: Remaining deductible and out-of-pocket maximum.
    """
    allowed = money(max(allowed, ZERO))
    billed = money(max(billed, allowed))
    inner = settle(allowed, rule, accumulators)
    balance = money(billed - allowed)

    steps = list(inner.steps)
    if balance > ZERO and not inner.not_covered:
        steps.append(
            f"This provider is outside your plan's network, so they can also bill you "
            f"the {_fmt(balance)} difference between their {_fmt(billed)} charge and the "
            f"{_fmt(allowed)} your plan allows. That part does not count toward your "
            "deductible or your out-of-pocket maximum."
        )

    if inner.not_covered:
        # Nothing is covered, so the member owes the whole charge.
        return OutOfNetworkSettlement(
            member_pays=billed,
            plan_pays=ZERO,
            applied_to_deductible=ZERO,
            counts_toward_oop=ZERO,
            balance_billed=balance,
            steps=steps,
        )

    return OutOfNetworkSettlement(
        member_pays=money(inner.member_pays + balance),
        plan_pays=inner.plan_pays,
        applied_to_deductible=inner.applied_to_deductible,
        counts_toward_oop=inner.member_pays,
        balance_billed=balance,
        steps=steps,
    )
