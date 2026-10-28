"""The knowledge-base catalogue: which documents MemberAssist may answer from.

Two kinds of document make up the knowledge base.

* **Plan documents** — one Summary of Benefits and Coverage per plan we sell.
  These carry a real ``plan_id`` and are only ever retrieved for the member who
  holds that plan. Their URLs come from ``data/mappings/selected_plans.csv``,
  which ``fetch_plans`` wrote from the CMS filings, so the chain from the
  federal filing to the answer a member reads is traceable.
* **Shared documents** — glossaries, appeal rights, Medicare coverage policy and
  one commercial insurer's prior-authorization policy. These carry
  ``plan_id = "ALL"`` and are retrievable for every member.

Every entry below was fetched once before it was written down, and the status of
that probe is recorded in ``docs/data_sources.md``. Nothing is listed on the
strength of a URL that looks plausible.

**Member data never appears here.** The index holds published rules only; a
member's own claims, accumulators and plan enrolment are read by tools that
query PostgreSQL behind the gateway. That split is what keeps personal data out
of the vector store entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ``plan_id`` for a document that applies to every member.
ALL_PLANS = "ALL"

DocType = Literal["sbc", "glossary", "appeals", "coverage_policy", "pa_policy", "faq"]
FetchKind = Literal["pdf", "html", "authored"]


@dataclass(frozen=True)
class KbSource:
    """One document declared for the knowledge base.

    Attributes:
        doc_id: Stable identifier; also the chunk-id prefix and the filename stem.
        title: Human title, shown to members on a citation chip.
        doc_type: Which kind of document this is, used for filtering and display.
        plan_id: A real plan id, or ``ALL`` for a shared document.
        source_url: Where it came from, shown to the member as a clickable source.
        licence_note: Copyright or reuse position, recorded for every source.
        kind: How to fetch it — a PDF, an HTML page, or a file we authored.
        publisher: Who published it.
        local_name: Filename under ``data/kb/files`` (authored files keep theirs).
        html_selectors: Candidate CSS selectors for the main content, best first.
        page_range: 1-based inclusive page window for a long PDF, or None for all.
        page_range_reason: Why that window, when one is used.
    """

    doc_id: str
    title: str
    doc_type: DocType
    plan_id: str
    source_url: str
    licence_note: str
    kind: FetchKind
    publisher: str
    local_name: str
    html_selectors: tuple[str, ...] = ()
    page_range: tuple[int, int] | None = None
    page_range_reason: str | None = None


# Licence positions, written once and referenced below so the wording cannot
# drift between two documents from the same publisher.
_US_GOV = (
    "Work of the U.S. federal government. Not subject to domestic copyright "
    "(17 U.S.C. 105); reproduced for a non-commercial student project."
)
_ISSUER_SBC = (
    "Published by the issuer as the federally required Summary of Benefits and "
    "Coverage for a plan sold on the Marketplace. Copyright of the issuer; "
    "quoted and cited for the plan it describes, not redistributed."
)
_AETNA_CPB = (
    "Aetna Clinical Policy Bulletin, publicly posted by Aetna Inc. Copyright of "
    "Aetna; quoted and cited as an example of a commercial prior-authorization "
    "policy, not redistributed."
)
_TEAMMATE = (
    "Written by the MemberAssist team (Person B) for this project. Every figure in its "
    "timelines table was verified against the HealthCare.gov pages it cites on 2026-10-28; "
    "see docs/data_sources.md."
)


#: Documents that apply to every member, in the order the manifest lists them.
SHARED_SOURCES: tuple[KbSource, ...] = (
    KbSource(
        doc_id="cms_uniform_glossary",
        title="Uniform Glossary of Coverage and Medical Terms",
        doc_type="glossary",
        plan_id=ALL_PLANS,
        source_url="https://www.cms.gov/CCIIO/Resources/Forms-Reports-and-Other-Resources/Downloads/uniform-glossary-final.pdf",
        licence_note=_US_GOV,
        kind="pdf",
        publisher="CMS / U.S. Department of Labor",
        local_name="cms_uniform_glossary.pdf",
    ),
    KbSource(
        doc_id="hcgov_glossary_deductible",
        title="Deductible — HealthCare.gov glossary",
        doc_type="glossary",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/glossary/deductible/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_glossary_deductible.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="hcgov_glossary_copayment",
        title="Copayment — HealthCare.gov glossary",
        doc_type="glossary",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/glossary/co-payment/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_glossary_copayment.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="hcgov_glossary_coinsurance",
        title="Coinsurance — HealthCare.gov glossary",
        doc_type="glossary",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/glossary/co-insurance/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_glossary_coinsurance.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="hcgov_glossary_oop_maximum",
        title="Out-of-pocket maximum — HealthCare.gov glossary",
        doc_type="glossary",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/glossary/out-of-pocket-maximum-limit/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_glossary_oop_maximum.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="hcgov_appeals_internal",
        title="Internal appeals — how to appeal an insurance company decision",
        doc_type="appeals",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_appeals_internal.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="hcgov_appeals_external",
        title="External review — appealing to an independent reviewer",
        doc_type="appeals",
        plan_id=ALL_PLANS,
        source_url="https://www.healthcare.gov/appeal-insurance-company-decision/external-review/",
        licence_note=_US_GOV,
        kind="html",
        publisher="HealthCare.gov (CMS)",
        local_name="hcgov_appeals_external.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="medicare_ncd_220_2_mri",
        title="Medicare National Coverage Determination 220.2 — Magnetic Resonance Imaging",
        doc_type="coverage_policy",
        plan_id=ALL_PLANS,
        source_url="https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=177",
        licence_note=_US_GOV,
        kind="html",
        publisher="CMS Medicare Coverage Database",
        local_name="medicare_ncd_220_2_mri.html",
        # The coverage database renders its policy text into a results panel
        # rather than a semantic <main>, so the id is tried before the tag.
        html_selectors=("#ncdContent", "div.pageContent", "main", "article"),
    ),
    KbSource(
        doc_id="medicare_bpm_ch15_therapy",
        title="Medicare Benefit Policy Manual, Chapter 15 — outpatient therapy services",
        doc_type="coverage_policy",
        plan_id=ALL_PLANS,
        source_url="https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c15.pdf",
        licence_note=_US_GOV,
        kind="pdf",
        publisher="CMS",
        local_name="medicare_bpm_ch15_therapy.pdf",
        page_range=(1, 2),
        page_range_reason=(
            "Placeholder replaced at fetch time by the outpatient-therapy window located "
            "in the chapter's own table of contents; see locate_therapy_pages()."
        ),
    ),
    # The specification's "commercial insurer policy on advanced-imaging prior
    # authorization" slot. Aetna's Clinical Policy Bulletin 0105 (breast MRI)
    # was tried here first and removed: at twenty thousand words of MRI clinical
    # criteria it supplied every single retrieval candidate for any imaging
    # question, so the member's own plan never reached the reranker. This page
    # is the actual precertification policy and is proportionate to its role.
    KbSource(
        doc_id="aetna_precertification_lists",
        title="Aetna precertification lists for commercial plans",
        doc_type="pa_policy",
        plan_id=ALL_PLANS,
        source_url="https://www.aetna.com/health-care-professionals/precertification/precertification-lists.html",
        licence_note=_AETNA_CPB,
        kind="html",
        publisher="Aetna Inc.",
        local_name="aetna_precertification_lists.html",
        html_selectors=("main", "article", "div.content"),
    ),
    KbSource(
        doc_id="memberassist_member_faq",
        title="MemberAssist member FAQ — claims, appeals, timelines and limits",
        doc_type="faq",
        plan_id=ALL_PLANS,
        source_url="https://github.com/mra-2007/memeberassist/blob/main/data/kb/authored/member_faq.md",
        licence_note=_TEAMMATE,
        kind="authored",
        publisher="MemberAssist team (Person B)",
        local_name="member_faq.md",
    ),
)


def sbc_source(
    *, plan_id: str, plan_name: str, issuer: str, sbc_url: str, local_name: str
) -> KbSource:
    """Build the catalogue entry for one plan's Summary of Benefits and Coverage."""
    return KbSource(
        doc_id=f"sbc_{plan_id}",
        title=f"Summary of Benefits and Coverage — {plan_name}",
        doc_type="sbc",
        plan_id=plan_id,
        source_url=sbc_url,
        licence_note=_ISSUER_SBC,
        kind="pdf",
        publisher=issuer,
        local_name=local_name,
    )
