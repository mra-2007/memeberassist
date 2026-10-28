"""Cross-encoder reranking: BAAI/bge-reranker-base, run locally.

The reranker reads the query and a candidate passage together, which lets it
judge relevance that a single-vector embedding cannot. It is the last ranking
step before the top five chunks go to the language model, and its top score is
what the confidence check reads.

**About the scale of these scores.** The model has one output head, and
``predict`` puts a sigmoid over it, so a score is between 0 and 1. The useful
range is not spread evenly. Measured on this knowledge base: a glossary question
against its own definition scores above 0.97, while a real coverage question
against the correct Summary of Benefits and Coverage row scores in the hundredths
or lower, because a terse benefit row shares little surface vocabulary with the
way a member asks about it. Ranking stays correct — the right chunk beats the
wrong one by orders of magnitude — but an absolute threshold must be calibrated
on real questions rather than assumed from the 0-1 range. That calibration is
``app/rag/confidence.py``, and it records the date and the data it was fitted on.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

#: The reranker the confidence threshold was calibrated against. Recorded in the
#: index manifest alongside the embedding model.
RERANKER_MODEL = "BAAI/bge-reranker-base"


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    """Load the cross-encoder once per process."""
    logger.info("loading reranker %s", RERANKER_MODEL)
    model: CrossEncoder = CrossEncoder(RERANKER_MODEL)
    return model


def rerank(query: str, passages: list[str], *, batch_size: int = 16) -> list[float]:
    """Score each passage against the query.

    Args:
        query: The member's question, already rewritten to stand alone.
        passages: Candidate chunk texts.
        batch_size: Pairs per forward pass.

    Returns:
        One score per passage, in the order the passages were given, each in
        0-1 where higher is more relevant.
    """
    if not passages:
        return []
    scores = get_reranker().predict(
        [(query, passage) for passage in passages],
        batch_size=batch_size,
        show_progress_bar=False,
    )
    return [float(score) for score in scores]
