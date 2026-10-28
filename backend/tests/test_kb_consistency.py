"""The knowledge base must agree with the database, and with itself.

Specification 7.5: "every ``plan_benefits`` number must agree with that plan's
SBC text". That is what makes a cited answer trustworthy — the number a tool
calculates with and the number a document shows the member have to be the same
number, or the product is lying to someone.

These tests read the fetched documents and the real database, so they need
``manage.py fetch-kb`` to have run. They are marked ``slow`` where they parse a
PDF, and skipped with a clear message when the knowledge base is absent, rather
than failing in a way that looks like a code defect.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
import yaml
from sqlalchemy import select

from app.config import DATA_DIR
from app.db.models import Plan, PlanBenefit
from app.db.session import session_scope
from app.rag.parse import parse_pdf

KB_DIR = DATA_DIR / "kb"
MANIFEST = KB_DIR / "manifest.yaml"

#: Benefits whose SBC wording is checked against the database for every plan.
#: These are the ones a member actually asks about and the demo depends on.
CHECKED_BENEFITS = (
    "Specialist Visit",
    "Imaging (CT/PET Scans, MRIs)",
    "Emergency Room Services",
)


def _manifest() -> dict:
    if not MANIFEST.exists():
        pytest.skip(f"{MANIFEST} is missing — run `manage.py fetch-kb` first.")
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    """The knowledge-base manifest."""
    return _manifest()


@pytest.fixture(scope="module")
def sbc_text() -> dict[str, str]:
    """Each plan's SBC as one normalised string, keyed by plan id."""
    documents = _manifest()["documents"]
    texts: dict[str, str] = {}
    for entry in documents:
        if entry["doc_type"] != "sbc":
            continue
        path = DATA_DIR / entry["local_file"]
        if not path.exists():
            pytest.skip(f"{path} is missing — run `manage.py fetch-kb` first.")
        parsed = parse_pdf(
            path.read_bytes(),
            doc_id=entry["doc_id"],
            title=entry["title"],
            subject=entry["title"],
        )
        joined = " ".join(page.text + " " + " ".join(page.table_sentences) for page in parsed.pages)
        texts[str(entry["plan_id"])] = re.sub(r"\s+", " ", joined)
    return texts


class TestManifest:
    """The manifest is the committed record of what was fetched."""

    def test_the_knowledge_base_is_within_the_specified_size(self, manifest: dict) -> None:
        count = len(manifest["documents"])
        assert 10 <= count <= 20, f"specification 7.5 asks for 10-20 documents, found {count}"

    def test_every_document_records_its_provenance(self, manifest: dict) -> None:
        for entry in manifest["documents"]:
            for field in (
                "doc_id",
                "title",
                "doc_type",
                "plan_id",
                "source_url",
                "retrieved_on",
                "licence_note",
                "sha256",
            ):
                assert entry.get(field), f"{entry.get('doc_id')} is missing {field}"

    def test_generated_documents_are_labelled_as_such(self, manifest: dict) -> None:
        """A document we wrote must never look like a published source."""
        for entry in manifest["documents"]:
            if entry["doc_type"] == "faq":
                assert entry["generated"] is True, f"{entry['doc_id']} must be marked generated"
            else:
                assert entry["generated"] is False

    def test_every_plan_in_the_database_has_an_sbc(self, manifest: dict) -> None:
        with session_scope() as session:
            plan_ids = set(session.scalars(select(Plan.plan_id)))
        with_sbc = {str(e["plan_id"]) for e in manifest["documents"] if e["doc_type"] == "sbc"}
        assert plan_ids <= with_sbc, f"plans without an SBC: {sorted(plan_ids - with_sbc)}"

    def test_shared_documents_are_marked_all(self, manifest: dict) -> None:
        for entry in manifest["documents"]:
            if entry["doc_type"] != "sbc":
                assert entry["plan_id"] == "ALL", f"{entry['doc_id']} should be plan_id ALL"

    def test_no_member_identifier_appears_in_the_manifest(self, manifest: dict) -> None:
        """Member data must never enter the index, so it must not reach the manifest."""
        text = yaml.safe_dump(manifest)
        assert not re.search(r"\bM1[0-9]{4}\b", text)


@pytest.mark.slow
class TestPlanBenefitsAgreeWithTheSbc:
    """The database's cost sharing must match what the plan's own document says."""

    def test_the_deductible_in_the_database_appears_in_the_sbc(
        self, sbc_text: dict[str, str]
    ) -> None:
        with session_scope() as session:
            plans = list(session.scalars(select(Plan)))
        assert plans, "no plans in the database — run `manage.py seed`"
        for plan in plans:
            text = sbc_text.get(plan.plan_id)
            assert text, f"no SBC parsed for {plan.plan_id}"
            formatted = f"${plan.deductible_ind:,.0f}"
            assert formatted in text, (
                f"{plan.plan_id}: database deductible {formatted} does not appear in its SBC"
            )

    def test_the_out_of_pocket_maximum_in_the_database_appears_in_the_sbc(
        self, sbc_text: dict[str, str]
    ) -> None:
        with session_scope() as session:
            plans = list(session.scalars(select(Plan)))
        for plan in plans:
            text = sbc_text[plan.plan_id]
            formatted = f"${plan.oop_max_ind:,.0f}"
            assert formatted in text, (
                f"{plan.plan_id}: database out-of-pocket maximum {formatted} "
                "does not appear in its SBC"
            )

    @pytest.mark.parametrize("benefit_name", CHECKED_BENEFITS)
    def test_a_benefits_cost_sharing_appears_in_the_sbc(
        self, sbc_text: dict[str, str], benefit_name: str
    ) -> None:
        """The stored copay or coinsurance must be findable in the plan's own text."""
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(PlanBenefit).where(
                        PlanBenefit.benefit_name == benefit_name,
                        PlanBenefit.network_tier == "in_network",
                    )
                )
            )
        if not rows:
            pytest.skip(f"no in-network rows stored for {benefit_name}")

        for row in rows:
            text = sbc_text.get(row.plan_id)
            if text is None:
                continue
            if not row.is_covered:
                continue
            if row.coinsurance is not None and row.coinsurance > 0:
                percentage = f"{row.coinsurance * 100:.0f}%"
                assert percentage in text, (
                    f"{row.plan_id} {benefit_name}: stored coinsurance {percentage} "
                    f"is not in the SBC text"
                )
            elif row.copay is not None and row.copay > 0:
                amount = f"${row.copay:,.0f}"
                assert amount in text, (
                    f"{row.plan_id} {benefit_name}: stored copay {amount} is not in the SBC text"
                )


@pytest.mark.slow
class TestTheSbcSaysWhatWeClaimItSays:
    """Facts the test set and the demo depend on, asserted against the document."""

    HERO_PLAN = "47501TX0040002"

    def test_adult_dental_care_is_listed_as_excluded(self, sbc_text: dict[str, str]) -> None:
        """T35 expects "not covered" with a citation, so the exclusion must be real."""
        text = sbc_text[self.HERO_PLAN]
        assert "Dental care (Adult)" in text

    def test_lasik_is_absent_from_every_plan_document(self, sbc_text: dict[str, str]) -> None:
        """T36 expects "not found", which is only correct if the word is truly absent."""
        for plan_id, text in sbc_text.items():
            assert "lasik" not in text.lower(), f"LASIK appears in {plan_id}'s SBC"

    def test_only_non_emergency_travel_care_is_excluded(self, sbc_text: dict[str, str]) -> None:
        """T37 is a partial-information case; this is what makes it one."""
        text = sbc_text[self.HERO_PLAN]
        assert "Non-emergency care when traveling outside the U.S." in text

    def test_imaging_is_priced_as_coinsurance_after_the_deductible(
        self, sbc_text: dict[str, str]
    ) -> None:
        """The demo's cost question depends on this plan design existing."""
        text = sbc_text[self.HERO_PLAN]
        assert "Imaging (CT/PET scans, MRIs)" in text
        assert "40% coinsurance" in text

    def test_the_hero_plans_deductible_is_four_thousand(self, sbc_text: dict[str, str]) -> None:
        with session_scope() as session:
            plan = session.scalar(select(Plan).where(Plan.plan_id == self.HERO_PLAN))
        assert plan is not None
        assert plan.deductible_ind == Decimal("4000.00")
        assert "$4,000" in sbc_text[self.HERO_PLAN]
