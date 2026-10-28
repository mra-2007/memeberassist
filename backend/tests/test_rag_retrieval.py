"""Retrieval tests: the plan filter, fusion, the per-document cap, confidence.

The plan-filter tests are the important ones. They are the Phase 2 half of
security invariant 4 — who the member is, and therefore which plan's documents
they may read, comes from the session and never from the question. A failure
here is a confidentiality failure, not a ranking curiosity.

Fusion, the cap and the confidence verdict are tested on constructed inputs, so
they run in the fast suite without loading a model. The tests that need the real
index and the real models carry the ``slow`` marker.
"""

from __future__ import annotations

import pytest

from app.rag.chunk import Chunk
from app.rag.confidence import CONFIDENCE_THRESHOLD, confidence_verdict
from app.rag.index_store import tokenize
from app.rag.retrieve import (
    ALL_PLANS,
    MAX_CHUNKS_PER_SHARED_DOC,
    OWN_PLAN_WEIGHT,
    RRF_K,
    Retrieved,
    _cap_per_document,
    allowed_plan_ids,
    reciprocal_rank_fusion,
    rerank_passage,
)

HERO_PLAN = "47501TX0040002"
OTHER_PLAN = "33602TX0461124"


def make_chunk(
    chunk_id: str,
    *,
    plan_id: str = ALL_PLANS,
    doc_id: str = "doc",
    page: int = 1,
    text: str = "some text",
    title: str = "Title",
    section: str = "Section",
) -> Chunk:
    """A chunk with sensible defaults, for ranking tests."""
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        plan_id=plan_id,
        doc_type="sbc",
        title=title,
        section=section,
        page=page,
        source_file="kb/files/x.pdf",
        source_url="https://example.invalid/x",
        text=text,
        kind="prose",
        word_count=len(text.split()),
    )


def make_result(chunk: Chunk, *, rerank: float) -> Retrieved:
    """A reranked result, for cap tests."""
    return Retrieved(
        chunk=chunk,
        vector_score=0.7,
        vector_rank=1,
        bm25_score=None,
        bm25_rank=None,
        rrf_score=0.01,
        rerank_score=rerank,
    )


class TestPlanFilter:
    """A member may read their own plan's documents and the shared ones. No others."""

    def test_a_plan_sees_itself_and_the_shared_documents(self) -> None:
        assert set(allowed_plan_ids(HERO_PLAN)) == {HERO_PLAN, ALL_PLANS}

    def test_another_plan_is_never_allowed(self) -> None:
        assert OTHER_PLAN not in allowed_plan_ids(HERO_PLAN)

    def test_no_plan_means_shared_documents_only(self) -> None:
        assert allowed_plan_ids(None) == [ALL_PLANS]
        assert allowed_plan_ids("") == [ALL_PLANS]

    def test_all_plans_as_a_session_plan_does_not_widen_access(self) -> None:
        """Passing "ALL" must not be a way to read every plan's documents."""
        assert allowed_plan_ids(ALL_PLANS) == [ALL_PLANS]


class TestFusion:
    """Reciprocal rank fusion merges by rank, because the scores are not comparable."""

    def test_a_chunk_found_by_both_searches_outranks_one_found_by_either(self) -> None:
        both = make_chunk("both")
        vector_only = make_chunk("vector_only")
        bm25_only = make_chunk("bm25_only")
        fused = reciprocal_rank_fusion(
            [(both, 0.8), (vector_only, 0.7)],
            [(both, 12.0), (bm25_only, 11.0)],
        )
        assert fused[0].chunk.chunk_id == "both"
        assert fused[0].found_by == "both"

    def test_rank_contributions_use_the_specified_constant(self) -> None:
        chunk = make_chunk("a")
        fused = reciprocal_rank_fusion([(chunk, 0.9)], [])
        assert fused[0].rrf_score == pytest.approx(1.0 / (RRF_K + 1))

    def test_the_members_own_plan_is_weighted_above_shared_documents(self) -> None:
        """Equal rank, so only the own-plan weight can separate them."""
        own = make_chunk("own", plan_id=HERO_PLAN, doc_id="sbc")
        shared = make_chunk("shared", plan_id=ALL_PLANS, doc_id="manual")
        fused = reciprocal_rank_fusion([(own, 0.7), (shared, 0.7)], [], plan_id=HERO_PLAN)
        by_id = {r.chunk.chunk_id: r for r in fused}
        assert by_id["own"].rrf_score == pytest.approx(OWN_PLAN_WEIGHT / (RRF_K + 1))
        assert by_id["shared"].rrf_score == pytest.approx(1.0 / (RRF_K + 2))

    def test_without_a_session_plan_nothing_is_weighted_up(self) -> None:
        own = make_chunk("own", plan_id=HERO_PLAN)
        fused = reciprocal_rank_fusion([(own, 0.7)], [], plan_id=None)
        assert fused[0].rrf_score == pytest.approx(1.0 / (RRF_K + 1))

    def test_found_by_reports_the_search_that_surfaced_a_chunk(self) -> None:
        chunk = make_chunk("a")
        assert reciprocal_rank_fusion([(chunk, 0.5)], [])[0].found_by == "vector"
        assert reciprocal_rank_fusion([], [(chunk, 5.0)])[0].found_by == "bm25"


class TestPerDocumentCap:
    """One long document must not supply every chunk of an answer."""

    def test_a_shared_document_is_capped_when_alternatives_exist(self) -> None:
        """Six high-scoring manual chunks must not take all five slots."""
        ranked = [
            make_result(make_chunk(f"m{n}", doc_id="long_manual", page=n), rerank=1.0 - n / 100)
            for n in range(6)
        ] + [
            make_result(make_chunk(f"g{n}", doc_id=f"glossary{n}", page=n), rerank=0.1 - n / 100)
            for n in range(3)
        ]
        kept = _cap_per_document(ranked, top_k=5, plan_id=HERO_PLAN)
        from_manual = [r for r in kept if r.chunk.doc_id == "long_manual"]
        assert len(from_manual) == MAX_CHUNKS_PER_SHARED_DOC
        assert len(kept) == 5

    def test_a_lower_scoring_other_document_still_reaches_the_answer(self) -> None:
        """The whole point: one document's volume must not crowd out the rest."""
        ranked = [
            make_result(make_chunk(f"m{n}", doc_id="long_manual", page=n), rerank=1.0 - n / 100)
            for n in range(6)
        ] + [make_result(make_chunk("g", doc_id="glossary"), rerank=0.01)]
        kept = _cap_per_document(ranked, top_k=5, plan_id=HERO_PLAN)
        assert any(r.chunk.doc_id == "glossary" for r in kept)

    def test_the_members_own_plan_document_is_exempt(self) -> None:
        """Several rows of the member's own SBC is the right answer, not a problem."""
        ranked = [
            make_result(
                make_chunk(f"c{n}", doc_id="sbc_hero", plan_id=HERO_PLAN, page=n),
                rerank=1.0 - n / 100,
            )
            for n in range(6)
        ]
        kept = _cap_per_document(ranked, top_k=5, plan_id=HERO_PLAN)
        assert len(kept) == 5
        assert all(r.chunk.doc_id == "sbc_hero" for r in kept)

    def test_the_cap_still_fills_the_slots_when_only_one_document_is_relevant(self) -> None:
        """A short answer is right, but padding with nothing at all is not."""
        ranked = [
            make_result(make_chunk(f"c{n}", doc_id="only_doc", page=n), rerank=1.0 - n / 100)
            for n in range(6)
        ]
        kept = _cap_per_document(ranked, top_k=5, plan_id=HERO_PLAN)
        assert len(kept) == 5

    def test_best_chunks_are_kept_first(self) -> None:
        ranked = [
            make_result(make_chunk("best", doc_id="a"), rerank=0.9),
            make_result(make_chunk("mid", doc_id="b"), rerank=0.5),
            make_result(make_chunk("worst", doc_id="c"), rerank=0.1),
        ]
        kept = _cap_per_document(ranked, top_k=2, plan_id=HERO_PLAN)
        assert [r.chunk.chunk_id for r in kept] == ["best", "mid"]


class TestRerankPassage:
    """The cross-encoder needs to know which document a passage came from."""

    def test_the_passage_carries_its_title_and_section(self) -> None:
        chunk = make_chunk(
            "a",
            title="Aetna Clinical Policy Bulletin 0105 — MRI of the Breast",
            section="Medical Necessity",
            text="MRI is covered when criteria are met.",
        )
        passage = rerank_passage(chunk)
        assert "MRI of the Breast" in passage
        assert "Medical Necessity" in passage
        assert "criteria are met" in passage


class TestConfidence:
    """The confidence check decides whether to answer at all."""

    def test_nothing_retrieved_is_not_answerable(self) -> None:
        verdict = confidence_verdict([])
        assert not verdict.answerable
        assert verdict.chunk_count == 0
        assert "nothing to answer from" in verdict.explain()

    def test_a_score_above_the_threshold_is_answerable(self) -> None:
        results = [make_result(make_chunk("a"), rerank=CONFIDENCE_THRESHOLD + 0.01)]
        assert confidence_verdict(results).answerable

    def test_a_score_below_the_threshold_is_not(self) -> None:
        results = [make_result(make_chunk("a"), rerank=CONFIDENCE_THRESHOLD / 2)]
        verdict = confidence_verdict(results)
        assert not verdict.answerable
        assert "offer a human" in verdict.explain()

    def test_the_threshold_is_inclusive(self) -> None:
        results = [make_result(make_chunk("a"), rerank=CONFIDENCE_THRESHOLD)]
        assert confidence_verdict(results).answerable

    def test_the_best_score_decides_not_the_first(self) -> None:
        results = [
            make_result(make_chunk("a"), rerank=CONFIDENCE_THRESHOLD / 2),
            make_result(make_chunk("b"), rerank=CONFIDENCE_THRESHOLD + 0.5),
        ]
        assert confidence_verdict(results).answerable

    def test_the_verdict_explains_itself_with_its_numbers(self) -> None:
        """The Security view shows this, so it has to be auditable."""
        results = [make_result(make_chunk("a"), rerank=0.42)]
        explanation = confidence_verdict(results).explain()
        assert "0.42" in explanation
        assert "2026-10-28" in explanation


class TestTokenizer:
    """BM25 turns on exactly the tokens a benefit question is made of."""

    def test_percentages_survive_tokenisation(self) -> None:
        assert "40%" in tokenize("you pay 40% coinsurance")

    def test_money_survives_tokenisation(self) -> None:
        assert "$500" in tokenize("a $500 copay applies")

    def test_procedure_codes_survive_tokenisation(self) -> None:
        assert "73721" in tokenize("procedure 73721 is an MRI")

    def test_tokens_are_lower_cased(self) -> None:
        assert tokenize("MRI Imaging") == ["mri", "imaging"]
