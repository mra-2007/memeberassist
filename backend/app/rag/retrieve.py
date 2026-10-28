"""Hybrid retrieval: vector search and BM25, fused, then reranked.

The pipeline is section 9 of the specification, in order:

1. filter to ``plan_id in {the member's plan, "ALL"}``;
2. vector top 20 (query carries bge's retrieval instruction) and BM25 top 20;
3. merge with reciprocal rank fusion, k = 60;
4. rerank the merged candidates with the cross-encoder and keep the best 5.

**The plan filter is applied inside the vector query and again when reading
BM25 results, and it comes from the session.** It is never taken from the
model's output or from anything the member typed. A member asking "show me the
Gold plan's rates" cannot reach another plan's documents: the filter is a
parameter of this function, and `tests/test_rag_retrieval.py` asserts that no
chunk from another plan is ever returned.

Why both searches. Vector search finds "what will an MRI cost me" against a row
phrased as imaging; BM25 finds "73721" and "40%" and "$500", which an embedding
blurs. Each covers the other's weakness, which is why the specification asks
for both rather than either.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from chromadb.api.types import Where

from app.ai.embeddings import encode_query
from app.ai.reranker import rerank
from app.rag.chunk import Chunk
from app.rag.index_store import Bm25Index, get_collection, load_bm25

logger = logging.getLogger(__name__)

#: Reciprocal rank fusion constant from the specification.
RRF_K = 60

#: Candidates taken from each search before fusion, and kept after reranking.
VECTOR_TOP_K = 20
BM25_TOP_K = 20
FINAL_TOP_K = 5

#: Extra weight on the member's own plan documents during fusion.
#:
#: "Does my plan cover a knee MRI" is answered by the member's own Summary of
#: Benefits and Coverage. The shared documents are context: a Medicare manual
#: and one insurer's breast-MRI bulletin describe how such rules are written,
#: not what this member's plan says. Without this weight the shared documents
#: win on sheer volume -- two of them contribute 187 of the index's chunks,
#: against 15 for the member's own plan -- and the first build answered three
#: coverage questions out of the wrong document entirely.
#:
#: It is a weight, not a filter: a glossary question still reaches the glossary,
#: because relevance dominates a 1.5x rank bonus.
OWN_PLAN_WEIGHT = 1.5

#: Most chunks any one shared document may contribute to a single answer.
#:
#: Without a cap, a long document monopolises every question in its subject
#: area. The Aetna bulletin on breast MRI is twenty thousand words of MRI
#: vocabulary, and on "is a knee MRI covered?" it supplied all five retrieved
#: chunks, pushing out the member's own plan row that actually answers the
#: question. Volume is not relevance, and five citations from one bulletin is a
#: worse answer for a member than a spread across the documents that apply.
#:
#: The member's own plan documents are exempt: when the question is about their
#: plan, several rows of their own Summary of Benefits and Coverage is the right
#: answer, not a diversity problem.
MAX_CHUNKS_PER_SHARED_DOC = 2

#: ``plan_id`` of a document that applies to every member.
ALL_PLANS = "ALL"


@dataclass(frozen=True)
class Retrieved:
    """One retrieved chunk, with every score that ranked it.

    Attributes:
        chunk: The chunk itself, with its citation metadata.
        vector_score: Cosine similarity, or None when only BM25 found it.
        vector_rank: 1-based rank in the vector search, or None.
        bm25_score: BM25 score, or None when only the vector search found it.
        bm25_rank: 1-based rank in the BM25 search, or None.
        rrf_score: Fused score that decided which candidates were reranked.
        rerank_score: Cross-encoder score, which the confidence check reads.
    """

    chunk: Chunk
    vector_score: float | None
    vector_rank: int | None
    bm25_score: float | None
    bm25_rank: int | None
    rrf_score: float
    rerank_score: float | None = None

    @property
    def found_by(self) -> str:
        """Which search(es) surfaced this chunk — useful in the Security view."""
        if self.vector_rank is not None and self.bm25_rank is not None:
            return "both"
        return "vector" if self.vector_rank is not None else "bm25"

    def citation(self) -> dict[str, str | int]:
        """What the UI needs to render a source chip."""
        return {
            "doc_id": self.chunk.doc_id,
            "title": self.chunk.title,
            "section": self.chunk.section,
            "page": self.chunk.page,
            "source_url": self.chunk.source_url,
        }


def _as_int(value: object) -> int:
    """Read an integer out of Chroma metadata, whose values are a wide union."""
    if isinstance(value, bool):  # bool is an int subclass; treat it as a bug
        raise TypeError("expected an integer in chunk metadata, got a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"expected an integer in chunk metadata, got {type(value).__name__}")


def _chunk_from_metadata(text: str, metadata: Mapping[str, object]) -> Chunk:
    """Rebuild a Chunk from what Chroma stored alongside the text."""
    return Chunk(
        chunk_id=str(metadata["chunk_id"]),
        doc_id=str(metadata["doc_id"]),
        plan_id=str(metadata["plan_id"]),
        doc_type=str(metadata["doc_type"]),
        title=str(metadata["title"]),
        section=str(metadata["section"]),
        page=_as_int(metadata["page"]),
        source_file=str(metadata["source_file"]),
        source_url=str(metadata["source_url"]),
        text=text,
        kind=str(metadata["kind"]),
        word_count=_as_int(metadata["word_count"]),
    )


def allowed_plan_ids(plan_id: str | None) -> list[str]:
    """The plan ids a session may read: its own plan plus the shared documents."""
    if not plan_id or plan_id == ALL_PLANS:
        return [ALL_PLANS]
    return [plan_id, ALL_PLANS]


def vector_search(
    query: str, *, plan_id: str | None, limit: int = VECTOR_TOP_K
) -> list[tuple[Chunk, float]]:
    """Vector search, filtered to the session's plan and the shared documents."""
    collection = get_collection()
    result = collection.query(
        query_embeddings=[encode_query(query).tolist()],
        n_results=limit,
        where=cast("Where", {"plan_id": {"$in": allowed_plan_ids(plan_id)}}),
        include=["documents", "metadatas", "distances"],
    )
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]

    found: list[tuple[Chunk, float]] = []
    for text, metadata, distance in zip(documents, metadatas, distances, strict=True):
        # The collection is cosine; Chroma reports distance, so similarity is 1 - d.
        found.append((_chunk_from_metadata(str(text), dict(metadata)), 1.0 - float(distance)))
    return found


def bm25_search(
    query: str,
    *,
    plan_id: str | None,
    index: Bm25Index | None = None,
    limit: int = BM25_TOP_K,
) -> list[tuple[Chunk, float]]:
    """BM25 search, then the same plan filter applied to its results.

    BM25 has no metadata filter of its own, so it is over-fetched and filtered
    afterwards. Filtering after the fact is what keeps the plan rule in one
    place instead of two subtly different ones.
    """
    index = index or load_bm25()
    allowed = set(allowed_plan_ids(plan_id))
    # Over-fetch, because the discarded hits are other plans' documents.
    hits = index.search(query, limit=limit * 4)
    if not hits:
        return []

    collection = get_collection()
    stored = collection.get(
        ids=[chunk_id for chunk_id, _ in hits], include=["documents", "metadatas"]
    )
    documents = stored.get("documents")
    metadatas = stored.get("metadatas")
    if documents is None or metadatas is None:
        raise RuntimeError(
            "Chroma returned no documents or metadata for ids the BM25 index holds; "
            "the two indexes are out of step -- rebuild with `manage.py build-index --force`."
        )
    by_id: dict[str, tuple[str, Mapping[str, object]]] = {
        str(metadata["chunk_id"]): (str(text), metadata)
        for text, metadata in zip(documents, metadatas, strict=True)
    }

    found: list[tuple[Chunk, float]] = []
    for chunk_id, score in hits:
        entry = by_id.get(chunk_id)
        if entry is None:
            continue
        text, metadata = entry
        if str(metadata["plan_id"]) not in allowed:
            continue
        found.append((_chunk_from_metadata(text, metadata), score))
        if len(found) >= limit:
            break
    return found


def _weight_for(chunk: Chunk, plan_id: str | None) -> float:
    """Fusion weight for a chunk: the member's own plan documents count more."""
    if plan_id and chunk.plan_id == plan_id and chunk.plan_id != ALL_PLANS:
        return OWN_PLAN_WEIGHT
    return 1.0


def reciprocal_rank_fusion(
    vector_hits: list[tuple[Chunk, float]],
    bm25_hits: list[tuple[Chunk, float]],
    *,
    k: int = RRF_K,
    plan_id: str | None = None,
) -> list[Retrieved]:
    """Merge two ranked lists into one, by rank rather than by score.

    Reciprocal rank fusion is used precisely because the two scores are not
    comparable: a cosine similarity and a BM25 score have different scales and
    different distributions. Rank is the common currency.

    The member's own plan documents carry ``OWN_PLAN_WEIGHT``; see that constant
    for the measurement that motivated it.
    """
    merged: dict[str, Retrieved] = {}

    for rank, (chunk, score) in enumerate(vector_hits, start=1):
        merged[chunk.chunk_id] = Retrieved(
            chunk=chunk,
            vector_score=score,
            vector_rank=rank,
            bm25_score=None,
            bm25_rank=None,
            rrf_score=_weight_for(chunk, plan_id) / (k + rank),
        )

    for rank, (chunk, score) in enumerate(bm25_hits, start=1):
        contribution = _weight_for(chunk, plan_id) / (k + rank)
        existing = merged.get(chunk.chunk_id)
        if existing is None:
            merged[chunk.chunk_id] = Retrieved(
                chunk=chunk,
                vector_score=None,
                vector_rank=None,
                bm25_score=score,
                bm25_rank=rank,
                rrf_score=contribution,
            )
            continue
        merged[chunk.chunk_id] = Retrieved(
            chunk=existing.chunk,
            vector_score=existing.vector_score,
            vector_rank=existing.vector_rank,
            bm25_score=score,
            bm25_rank=rank,
            rrf_score=existing.rrf_score + contribution,
        )

    return sorted(merged.values(), key=lambda r: -r.rrf_score)


def _cap_per_document(
    ranked: list[Retrieved], *, top_k: int, plan_id: str | None
) -> list[Retrieved]:
    """Take the best ``top_k`` chunks, letting no shared document dominate.

    Chunks are already in rerank order, so the first pass keeps the best ones
    within the cap. If that leaves fewer than ``top_k`` — a question only one
    document covers — the remainder is filled from what the cap held back,
    because a short answer from one good source beats padding with noise.
    """
    per_document: dict[str, int] = {}
    kept: list[Retrieved] = []
    overflow: list[Retrieved] = []

    for result in ranked:
        chunk = result.chunk
        own_plan = bool(plan_id) and chunk.plan_id == plan_id and chunk.plan_id != ALL_PLANS
        seen = per_document.get(chunk.doc_id, 0)
        if not own_plan and seen >= MAX_CHUNKS_PER_SHARED_DOC:
            overflow.append(result)
            continue
        per_document[chunk.doc_id] = seen + 1
        kept.append(result)
        if len(kept) == top_k:
            return kept

    for result in overflow:
        if len(kept) == top_k:
            break
        kept.append(result)
    return kept


def rerank_passage(chunk: Chunk) -> str:
    """What the cross-encoder reads: the chunk, prefixed by where it came from.

    The cross-encoder is given the document title and section as well as the
    text, because the passage alone does not say what document it belongs to.
    On "is a knee MRI covered?" the bare text let an insurer's *breast* MRI
    bulletin outrank the member's own plan, since both passages talk about MRI;
    with the title in view the bulletin drops out of the top five and the plan's
    own imaging row rises into it.
    """
    return f"{chunk.title} - {chunk.section}: {chunk.text}"


def retrieve(
    query: str,
    *,
    plan_id: str | None,
    top_k: int = FINAL_TOP_K,
    vector_k: int = VECTOR_TOP_K,
    bm25_k: int = BM25_TOP_K,
    bm25_index: Bm25Index | None = None,
) -> list[Retrieved]:
    """Retrieve the best chunks for a question, for one member's plan.

    Args:
        query: A standalone question. Follow-ups are rewritten before this.
        plan_id: The plan from the verified session, or None for shared only.
        top_k: How many chunks to keep after reranking.
        vector_k: Candidates from the vector search.
        bm25_k: Candidates from BM25.
        bm25_index: Pre-loaded BM25 index, to avoid reloading it per query.

    Returns:
        Up to ``top_k`` chunks, best first, each carrying every score that
        ranked it. Empty when nothing passed the plan filter.
    """
    vector_hits = vector_search(query, plan_id=plan_id, limit=vector_k)
    bm25_hits = bm25_search(query, plan_id=plan_id, index=bm25_index, limit=bm25_k)
    fused = reciprocal_rank_fusion(vector_hits, bm25_hits, plan_id=plan_id)
    if not fused:
        logger.info("no candidates for %r under plan %s", query[:80], plan_id)
        return []

    scores = rerank(query, [rerank_passage(candidate.chunk) for candidate in fused])
    scored = [
        Retrieved(
            chunk=candidate.chunk,
            vector_score=candidate.vector_score,
            vector_rank=candidate.vector_rank,
            bm25_score=candidate.bm25_score,
            bm25_rank=candidate.bm25_rank,
            rrf_score=candidate.rrf_score,
            rerank_score=score,
        )
        for candidate, score in zip(fused, scores, strict=True)
    ]
    scored.sort(key=lambda r: -(r.rerank_score or 0.0))

    kept = _cap_per_document(scored, top_k=top_k, plan_id=plan_id)
    leaked = [r.chunk.plan_id for r in kept if r.chunk.plan_id not in allowed_plan_ids(plan_id)]
    if leaked:
        # Belt and braces: this should be impossible, and if it ever happens it
        # is a confidentiality failure rather than a ranking curiosity.
        raise RuntimeError(
            f"Retrieval returned chunks for plan(s) {sorted(set(leaked))} "
            f"while the session's plan is {plan_id}."
        )
    return kept
