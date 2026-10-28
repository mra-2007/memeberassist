"""Where the index lives: a persistent Chroma collection and a BM25 index.

Both are built from exactly the same chunk list, so a chunk id returned by
either side resolves in the other. That is what makes reciprocal rank fusion
meaningful rather than approximate.

The manifest beside them records the embedding model, the chunker version and a
hash of every indexed document. ``index_is_current`` compares the manifest with
the knowledge base on disk, so a stale index is reported rather than queried —
the failure mode where an answer cites a document that has since changed is the
one worth engineering against.

Nothing here calls an LLM, and **no member data is ever written to the index**:
the only input is the published documents named in ``data/kb/manifest.yaml``.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from rank_bm25 import BM25Okapi

from app.ai.embeddings import EMBEDDING_MODEL
from app.ai.reranker import RERANKER_MODEL
from app.config import DATA_DIR
from app.rag.chunk import CHUNKER_VERSION, Chunk

logger = logging.getLogger(__name__)

INDEX_DIR = DATA_DIR / "index"
CHROMA_DIR = INDEX_DIR / "chroma"
BM25_PATH = INDEX_DIR / "bm25.pkl"
MANIFEST_PATH = INDEX_DIR / "index_manifest.json"

COLLECTION_NAME = "memberassist_kb"

#: Word tokens for BM25. Keeps digits and the per-cent sign, because "40%",
#: "73721" and "$500" are exactly the tokens a benefit question turns on.
_TOKEN = re.compile(r"[a-z0-9%$][a-z0-9%$./-]*")


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens for BM25, keeping numbers and percentages intact."""
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class IndexManifest:
    """What an index was built from, so staleness is detectable.

    Attributes:
        embedding_model: Model the vectors were produced with.
        reranker_model: Reranker the confidence threshold was calibrated for.
        chunker_version: Chunking shape version.
        document_hashes: ``doc_id`` to the SHA-256 recorded when it was indexed.
        chunk_count: Chunks in the index.
        built_on: ISO date of the build.
    """

    embedding_model: str
    reranker_model: str
    chunker_version: int
    document_hashes: dict[str, str]
    chunk_count: int
    built_on: str

    def to_json(self) -> dict[str, Any]:
        """Serialisable form written to ``index_manifest.json``."""
        return {
            "embedding_model": self.embedding_model,
            "reranker_model": self.reranker_model,
            "chunker_version": self.chunker_version,
            "document_hashes": self.document_hashes,
            "chunk_count": self.chunk_count,
            "built_on": self.built_on,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> IndexManifest:
        """Read a manifest written by a previous build."""
        return cls(
            embedding_model=payload["embedding_model"],
            reranker_model=payload["reranker_model"],
            chunker_version=int(payload["chunker_version"]),
            document_hashes=dict(payload["document_hashes"]),
            chunk_count=int(payload["chunk_count"]),
            built_on=payload["built_on"],
        )


def get_client() -> chromadb.ClientAPI:
    """Persistent Chroma client, with its own telemetry switched off."""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
    )


def get_collection(client: chromadb.ClientAPI | None = None) -> chromadb.Collection:
    """The knowledge-base collection, created on first use.

    Cosine distance is requested explicitly: the embeddings are unit-norm, so
    cosine and inner product agree, and naming it keeps the stored space
    independent of whatever Chroma's default happens to be in a given release.
    """
    client = client or get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
    )


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------
@dataclass
class Bm25Index:
    """A BM25 index over the same chunks as the vector collection.

    Attributes:
        chunk_ids: Chunk ids, positionally aligned with the BM25 corpus.
        model: The fitted BM25 model.
    """

    chunk_ids: list[str]
    model: BM25Okapi

    def search(self, query: str, limit: int) -> list[tuple[str, float]]:
        """Top ``limit`` chunk ids for a query, best first."""
        tokens = tokenize(query)
        if not tokens or not self.chunk_ids:
            return []
        scores = self.model.get_scores(tokens)
        ranked = sorted(zip(self.chunk_ids, scores, strict=True), key=lambda p: -p[1])
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:limit] if score > 0]


def save_bm25(chunks: list[Chunk], path: Path = BM25_PATH) -> None:
    """Fit BM25 over the chunk texts and write it to disk.

    Pickle is used because that is how ``rank_bm25`` objects persist; the file is
    written by our own build script into a git-ignored directory and read only by
    this module, so it is never a channel for untrusted input.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    corpus = [tokenize(chunk.text) for chunk in chunks]
    payload = {
        "chunk_ids": [chunk.chunk_id for chunk in chunks],
        "model": BM25Okapi(corpus) if corpus else None,
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("wrote BM25 index over %d chunks to %s", len(chunks), path)


def load_bm25(path: Path = BM25_PATH) -> Bm25Index:
    """Load the BM25 index.

    Raises:
        FileNotFoundError: when the index has not been built yet.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing — run `manage.py build-index` first.")
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    if payload["model"] is None:
        raise ValueError(f"{path} holds an empty BM25 index — rebuild it.")
    return Bm25Index(chunk_ids=list(payload["chunk_ids"]), model=payload["model"])


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def write_manifest(manifest: IndexManifest, path: Path = MANIFEST_PATH) -> None:
    """Write the index manifest next to the index."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_json(), indent=2), encoding="utf-8")


def read_manifest(path: Path = MANIFEST_PATH) -> IndexManifest | None:
    """Read the index manifest, or None when no index has been built."""
    if not path.exists():
        return None
    return IndexManifest.from_json(json.loads(path.read_text(encoding="utf-8")))


def staleness_reasons(kb_hashes: dict[str, str], path: Path = MANIFEST_PATH) -> list[str]:
    """Explain why the index does not match the knowledge base, if it does not.

    Args:
        kb_hashes: ``doc_id`` to SHA-256, from ``data/kb/manifest.yaml``.

    Returns:
        One sentence per reason to rebuild. Empty when the index is current.
    """
    manifest = read_manifest(path)
    if manifest is None:
        return ["no index has been built yet"]

    reasons: list[str] = []
    if manifest.embedding_model != EMBEDDING_MODEL:
        reasons.append(
            f"embedding model changed: indexed with {manifest.embedding_model}, "
            f"now configured as {EMBEDDING_MODEL}"
        )
    if manifest.reranker_model != RERANKER_MODEL:
        reasons.append(
            f"reranker changed: calibrated for {manifest.reranker_model}, "
            f"now configured as {RERANKER_MODEL}"
        )
    if manifest.chunker_version != CHUNKER_VERSION:
        reasons.append(
            f"chunker version changed: index built with v{manifest.chunker_version}, "
            f"code is v{CHUNKER_VERSION}"
        )

    indexed = manifest.document_hashes
    for doc_id, digest in sorted(kb_hashes.items()):
        if doc_id not in indexed:
            reasons.append(f"{doc_id} is in the knowledge base but not in the index")
        elif indexed[doc_id] != digest:
            reasons.append(f"{doc_id} has changed since it was indexed")
    for doc_id in sorted(set(indexed) - set(kb_hashes)):
        reasons.append(f"{doc_id} is in the index but no longer in the knowledge base")
    return reasons


def index_is_current(kb_hashes: dict[str, str], path: Path = MANIFEST_PATH) -> bool:
    """True when the index matches the knowledge base exactly."""
    return not staleness_reasons(kb_hashes, path)
