"""Print the demo scenario's real numbers.

    uv run python manage.py demo-facts

Used to write ``docs/demo_script.md`` and the Phase 1 evidence in
``PROGRESS.md``, and to re-check after any data change that the figures a
reviewer will see on screen are still the ones the documents claim.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import (
    Accumulator,
    Claim,
    Member,
    PaRequest,
    Plan,
    PlanBenefit,
    ProcedurePrice,
    Provider,
    Refill,
    User,
)
from app.db.session import session_scope

HERO = "M10234"
OTHER = "M10555"
IMAGING = "Imaging (CT/PET Scans, MRIs)"
SPOT_CHECK_CODES = ("73721", "99213", "85025", "27447")


def main() -> None:
    with session_scope() as session:
        hero = session.get(Member, HERO)
        if hero is None:
            raise SystemExit("Database is not seeded. Run `manage.py seed` first.")
        plan = session.get(Plan, hero.plan_id)
        accumulator = session.scalar(select(Accumulator).where(Accumulator.member_id == HERO))
        assert plan is not None and accumulator is not None

        print(f"HERO {hero.member_id}: {hero.full_name}, dob {hero.dob}")
        print(f"  address  {hero.address}, {hero.city}, {hero.state} {hero.zip_code}")
        print(f"  contact  {hero.phone} / {hero.email}")
        print(f"  plan     {plan.plan_id} {plan.issuer}")
        print(f"           {plan.name}")
        print(f"           {plan.metal_level} {plan.plan_type}, HSA-eligible: {plan.hsa_eligible}")
        print(
            f"  deductible ${plan.deductible_ind} (used ${accumulator.deductible_used}, "
            f"${plan.deductible_ind - accumulator.deductible_used} left)"
        )
        print(
            f"  oop max    ${plan.oop_max_ind} (used ${accumulator.oop_used}, "
            f"${plan.oop_max_ind - accumulator.oop_used} left)"
        )
        for tier in ("in_network", "out_of_network"):
            row = session.scalar(
                select(PlanBenefit).where(
                    PlanBenefit.plan_id == plan.plan_id,
                    PlanBenefit.benefit_name == IMAGING,
                    PlanBenefit.network_tier == tier,
                )
            )
            if row is not None:
                print(f"  imaging {tier:<15} {row.display_text!r} (covered: {row.is_covered})")

        print("\nHERO CLAIMS")
        providers = {p.npi: p for p in session.scalars(select(Provider))}
        for claim in session.scalars(
            select(Claim).where(Claim.member_id == HERO).order_by(Claim.service_date.desc())
        ):
            network = "in" if providers[claim.provider_npi].in_network else "OUT"
            flag = f"/{claim.denial_code}" if claim.denial_code else ""
            print(
                f"  {claim.claim_id:<10} {claim.service_date} {claim.hcpcs_code} "
                f"{claim.place_of_service:<8} {network:<3} "
                f"billed ${claim.billed:>8} allowed ${claim.allowed:>8} "
                f"plan ${claim.plan_paid:>8} you ${claim.member_resp:>8} "
                f"{claim.status}{flag} dx={claim.icd10_codes}"
            )

        pa = session.get(PaRequest, "PA-889")
        if pa is not None:
            print(
                f"\nPA-889  {pa.status}, code {pa.hcpcs_code}, submitted {pa.submitted_at:%Y-%m-%d}"
            )
            for item in pa.missing_items:
                print(f"  missing: {item}")

        refill = session.scalar(
            select(Refill).where(Refill.member_id == HERO).order_by(Refill.next_due)
        )
        if refill is not None:
            print(f"\nNext refill  {refill.drug_name} (RxCUI {refill.rxcui}) due {refill.next_due}")

        other = session.get(Member, OTHER)
        if other is not None:
            other_claims = len(list(session.scalars(select(Claim).where(Claim.member_id == OTHER))))
            print(f"\nATTACK-TEST MEMBER {OTHER}: {other.full_name}, {other_claims} claims")

        print("\nPLANS")
        for row in session.scalars(select(Plan).order_by(Plan.plan_id)):
            print(
                f"  {row.plan_id} {row.metal_level:<16} {row.plan_type:<4} "
                f"ded ${row.deductible_ind:>8} oop ${row.oop_max_ind:>9} "
                f"HSA={'y' if row.hsa_eligible else 'n'} {row.issuer[:34]}"
            )

        print("\nPRICE SPOT CHECK")
        for code in SPOT_CHECK_CODES:
            for price in session.scalars(
                select(ProcedurePrice)
                .where(ProcedurePrice.hcpcs_code == code)
                .order_by(ProcedurePrice.place_of_service)
            ):
                print(
                    f"  {price.hcpcs_code} {price.place_of_service:<8} "
                    f"allowed ${price.avg_allowed:>9} medicare pays ${price.avg_payment:>9} "
                    f"[{price.source}] {price.description[:32]}"
                )

        print("\nLOGINS")
        for user in session.scalars(select(User).order_by(User.role, User.username)):
            print(f"  {user.username:<10} {user.role:<9} {user.display_name}")


if __name__ == "__main__":
    main()
