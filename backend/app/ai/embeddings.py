"""Sentence embeddings for retrieval: BAAI/bge-small-en-v1.5, run locally.

This is a real local model, not an API call. It loads on first use and stays
loaded for the process, because loading costs about 20 seconds on this machine
and encoding a query costs milliseconds.

Two details that matter for retrieval quality and are easy to get wrong:

* **Queries take an instruction prefix, passages do not.** bge was trained
  asymmetrically; prefixing the passage side as well measurably degrades
  matching. ``encode_query`` adds the prefix and ``encode_passages`` does not,
  so a caller cannot mix them up.
* **Vectors are normalised**, so a dot product is a cosine similarity and the
  Chroma collection can use the cheaper inner-product space.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

#: The model the index is built with. Recorded in the index manifest; changing
#: it invalidates the index, which ``build_index`` detects and reports.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

#: bge's own retrieval instruction, applied to the query side only.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

#: Output dimensionality of bge-small-en-v1.5, asserted after loading so a
#: silently substituted model cannot corrupt an existing collection.
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """Load the embedding model once per process.

    Raises:
        RuntimeError: if the loaded model's width is not what the index expects.
    """
    logger.info("loading embedding model %s", EMBEDDING_MODEL)
    model = SentenceTransformer(EMBEDDING_MODEL)
    width = model.get_sentence_embedding_dimension()
    if width != EMBEDDING_DIM:
        raise RuntimeError(
            f"{EMBEDDING_MODEL} returned {width}-dimensional vectors, "
            f"but the index is built for {EMBEDDING_DIM}."
        )
    return model


def encode_passages(texts: list[str], *, batch_size: int = 32) -> np.ndarray:
    """Embed chunks for indexing. No instruction prefix — see the module docstring.

    Args:
        texts: Chunk texts.
        batch_size: Passages per forward pass.

    Returns:
        A ``(len(texts), 384)`` array of unit-norm float32 vectors.
    """
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    vectors = get_embedder().encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vectors, dtype=np.float32)


def encode_query(query: str) -> np.ndarray:
    """Embed one search query, with bge's retrieval instruction prefix."""
    vector = get_embedder().encode(
        f"{QUERY_INSTRUCTION}{query}",
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return np.asarray(vector, dtype=np.float32)
