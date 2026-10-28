"""Build the retrieval index from the fetched knowledge base.

Run from the repository root::

    uv run python manage.py build-index          # rebuild only if stale
    uv run python manage.py build-index --force  # always rebuild

The build is reproducible: the same documents and the same chunker produce the
same chunk ids, because a chunk id is a hash of its own text. Re-running when
nothing has changed is therefore a no-op, and the script says so rather than
spending four minutes re-embedding identical text.

**No member data enters the index.** The only inputs are the documents listed in
``data/kb/manifest.yaml``, every one of them published by CMS, an issuer or an
insurer, plus the FAQ we wrote. Personal facts are answered by tools that query
PostgreSQL behind the gateway.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from dataclasses import dataclass

import yaml
from rich.table import Table

from app.ai.embeddings import EMBEDDING_MODEL, encode_passages
from app.ai.reranker import RERANKER_MODEL
from app.config import DATA_DIR
from app.rag.chunk import CHUNKER_VERSION, Chunk, chunk_document
from app.rag.index_store import (
    COLLECTION_NAME,
    IndexManifest,
    get_client,
    get_collection,
    save_bm25,
    staleness_reasons,
    write_manifest,
)
from app.rag.parse import parse_html, parse_markdown, parse_pdf
from scripts._common import console, setup_logging

logger = logging.getLogger(__name__)

KB_MANIFEST = DATA_DIR / "kb" / "manifest.yaml"

#: Chunks embedded per Chroma add() call. Keeps peak memory modest on a
#: 14 GB machine while still batching enough to be fast.
UPSERT_BATCH = 128

#: A shared document holding more than this share of the index gets a warning.
DOMINANCE_SHARE = 0.25


@dataclass
class DocumentStats:
    """What one document contributed to the index."""

    doc_id: str
    plan_id: str
    doc_type: str
    pages: int
    words: int
    table_sentences: int
    chunks: int


def load_kb_manifest() -> list[dict[str, object]]:
    """Read the knowledge-base manifest.

    Raises:
        FileNotFoundError: when ``fetch-kb`` has not run.
    """
    if not KB_MANIFEST.exists():
        raise FileNotFoundError(f"{KB_MANIFEST} is missing — run `manage.py fetch-kb` first.")
    payload = yaml.safe_load(KB_MANIFEST.read_text(encoding="utf-8"))
    documents = payload.get("documents") or []
    if not documents:
        raise ValueError(f"{KB_MANIFEST} lists no documents.")
    return list(documents)


def parse_entry(entry: dict[str, object]) -> tuple[list[Chunk], DocumentStats]:
    """Parse and chunk one manifest entry.

    Raises:
        FileNotFoundError: when the local copy is missing.
    """
    doc_id = str(entry["doc_id"])
    title = str(entry["title"])
    plan_id = str(entry["plan_id"])
    doc_type = str(entry["doc_type"])
    local_file = str(entry["local_file"])
    source_url = str(entry["source_url"])
    path = DATA_DIR / local_file
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing — re-run `manage.py fetch-kb`.")

    fmt = str(entry.get("format", "pdf"))
    if fmt == "pdf":
        page_range = entry.get("page_range")
        window = (int(page_range[0]), int(page_range[1])) if page_range else None  # type: ignore[index]
        doc = parse_pdf(
            path.read_bytes(),
            doc_id=doc_id,
            title=title,
            subject=_table_subject(entry),
            page_range=window,
        )
    elif fmt == "html":
        selectors = tuple(str(s) for s in (entry.get("html_selectors") or ()))
        doc = parse_html(
            path.read_text(encoding="utf-8", errors="replace"),
            doc_id=doc_id,
            title=title,
            subject=_table_subject(entry),
            selectors=selectors,
        )
    else:
        doc = parse_markdown(
            path.read_text(encoding="utf-8", errors="replace"), doc_id=doc_id, title=title
        )

    chunks = chunk_document(
        doc,
        plan_id=plan_id,
        doc_type=doc_type,
        source_file=local_file,
        source_url=source_url,
    )
    stats = DocumentStats(
        doc_id=doc_id,
        plan_id=plan_id,
        doc_type=doc_type,
        pages=len(doc.pages),
        words=doc.word_count,
        table_sentences=doc.table_sentence_count,
        chunks=len(chunks),
    )
    return chunks, stats


def _table_subject(entry: dict[str, object]) -> str:
    """What a table sentence should call the document it came from.

    For a Summary of Benefits and Coverage this is the plan's own name, because
    "Under the Wellpoint Essential Bronze 4000 HSA plan, ..." is the sentence a
    member's question has to match.
    """
    title = str(entry["title"])
    prefix = "Summary of Benefits and Coverage — "
    return title[len(prefix) :] if title.startswith(prefix) else title


def build(*, force: bool) -> int:
    """Parse, chunk, embed and index the whole knowledge base."""
    entries = load_kb_manifest()
    kb_hashes = {str(e["doc_id"]): str(e["sha256"]) for e in entries}

    reasons = staleness_reasons(kb_hashes)
    if not reasons and not force:
        console.print(
            "[green]Index is current[/green] — same documents, same embedding model, "
            f"same chunker (v{CHUNKER_VERSION}). Nothing to rebuild; pass --force to rebuild anyway."
        )
        return 0
    if reasons:
        console.print("[bold]Rebuilding because:[/bold]")
        for reason in reasons:
            console.print(f"  - {reason}")
    else:
        console.print("[bold]Rebuilding (--force)[/bold]")

    started = time.perf_counter()
    all_chunks: list[Chunk] = []
    stats: list[DocumentStats] = []
    for entry in entries:
        chunks, document_stats = parse_entry(entry)
        if not chunks:
            console.print(f"  [red]{document_stats.doc_id} produced no chunks[/red]")
        all_chunks.extend(chunks)
        stats.append(document_stats)

    if not all_chunks:
        console.print("[red]No chunks were produced — refusing to write an empty index.[/red]")
        return 1

    _check_chunk_ids_unique(all_chunks)

    # A fresh collection rather than an upsert: chunk ids change when text
    # changes, so upserting would leave the superseded chunks in place.
    client = get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        logger.debug("no existing collection to delete")
    collection = get_collection(client)

    console.print(f"\nEmbedding {len(all_chunks):,} chunks with {EMBEDDING_MODEL} …")
    for start in range(0, len(all_chunks), UPSERT_BATCH):
        batch = all_chunks[start : start + UPSERT_BATCH]
        vectors = encode_passages([chunk.text for chunk in batch])
        collection.add(
            ids=[chunk.chunk_id for chunk in batch],
            documents=[chunk.text for chunk in batch],
            metadatas=[chunk.metadata() for chunk in batch],
            embeddings=vectors.tolist(),
        )
        console.print(f"  {min(start + len(batch), len(all_chunks)):>5,}/{len(all_chunks):,}")

    save_bm25(all_chunks)
    write_manifest(
        IndexManifest(
            embedding_model=EMBEDDING_MODEL,
            reranker_model=RERANKER_MODEL,
            chunker_version=CHUNKER_VERSION,
            document_hashes=kb_hashes,
            chunk_count=len(all_chunks),
            built_on=dt.date.today().isoformat(),
        )
    )

    _print_report(stats, all_chunks, elapsed=time.perf_counter() - started)
    stored = collection.count()
    if stored != len(all_chunks):
        console.print(f"[red]Chroma holds {stored} chunks but {len(all_chunks)} were built.[/red]")
        return 1
    return 0


def _check_chunk_ids_unique(chunks: list[Chunk]) -> None:
    """Fail loudly on a duplicate chunk id rather than silently losing a chunk.

    Chunk ids are content hashes, so a collision means two chunks hold identical
    text on the same page — which would be a chunking bug, not bad luck.
    """
    seen: dict[str, Chunk] = {}
    duplicates: list[str] = []
    for chunk in chunks:
        if chunk.chunk_id in seen:
            duplicates.append(f"{chunk.chunk_id} ({chunk.doc_id} p{chunk.page})")
        seen[chunk.chunk_id] = chunk
    if duplicates:
        raise ValueError(
            "Duplicate chunk ids, so identical text was chunked twice:\n  "
            + "\n  ".join(duplicates[:10])
        )


def _print_report(stats: list[DocumentStats], chunks: list[Chunk], *, elapsed: float) -> None:
    """Print what went into the index, per document and in total."""
    table = Table(title="Index contents")
    for column, justify in (
        ("Document", "left"),
        ("Plan", "left"),
        ("Type", "left"),
        ("Pages", "right"),
        ("Words", "right"),
        ("Table rows", "right"),
        ("Chunks", "right"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for row in sorted(stats, key=lambda s: (s.doc_type, s.doc_id)):
        table.add_row(
            row.doc_id,
            row.plan_id,
            row.doc_type,
            f"{row.pages:,}",
            f"{row.words:,}",
            f"{row.table_sentences:,}",
            f"{row.chunks:,}",
        )
    table.add_section()
    table.add_row(
        f"{len(stats)} documents",
        "",
        "",
        f"{sum(s.pages for s in stats):,}",
        f"{sum(s.words for s in stats):,}",
        f"{sum(s.table_sentences for s in stats):,}",
        f"{len(chunks):,}",
    )
    console.print(table)

    prose = [c for c in chunks if c.kind == "prose"]
    tables = [c for c in chunks if c.kind == "table"]
    words = sorted(c.word_count for c in chunks)
    median = words[len(words) // 2] if words else 0
    console.print(
        f"{len(prose):,} prose chunks and {len(tables):,} table chunks; "
        f"words per chunk min {words[0]} / median {median} / max {words[-1]}"
    )
    _warn_on_dominant_documents(stats, total_chunks=len(chunks))
    console.print(f"Built in {elapsed:.1f}s")


def _warn_on_dominant_documents(stats: list[DocumentStats], *, total_chunks: int) -> None:
    """Warn when one shared document holds enough of the index to crowd out the rest.

    This is a real failure mode, found the hard way: a single twenty-thousand-word
    bulletin supplied every retrieval candidate for a whole class of question,
    so the member's own plan document never reached the reranker. A share report
    at build time makes that visible before it reaches an answer.
    """
    if not total_chunks:
        return
    shared = [s for s in stats if s.plan_id == "ALL"]
    noisy = [s for s in shared if s.chunks / total_chunks > DOMINANCE_SHARE]
    if not noisy:
        return
    console.print(
        f"\n[yellow]Document share warning[/yellow] — these shared documents each hold "
        f"more than {DOMINANCE_SHARE:.0%} of the index, so they can crowd a member's own "
        f"plan documents out of the candidate pool:"
    )
    for row in sorted(noisy, key=lambda s: -s.chunks):
        console.print(
            f"  {row.doc_id:34s} {row.chunks:>4} chunks  {row.chunks / total_chunks:>5.0%} "
            f"of the index"
        )
    console.print(
        "  [dim]Check with `manage.py ask-index` that a plan question still reaches the "
        "plan's own documents.[/dim]"
    )


def main(argv: list[str] | None = None) -> int:
    """Build the index, rebuilding only when something has changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="Rebuild even if the index is current."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return build(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
