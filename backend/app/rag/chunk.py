"""Split parsed documents into retrievable chunks.

The rules come from section 9 of the specification: chunk on headings, aim for
300-500 words with about 60 words of overlap, never split a table row, and put
enough metadata on every chunk that an answer can cite it.

Two decisions worth stating.

**A table sentence is atomic.** Each sentence produced by the parser already
carries its plan, its common medical event, its service and its payment columns.
Splitting one in half would produce a chunk that states a price without saying
what it is the price of — the exact failure the sentence form exists to prevent.
So a table sentence is placed whole or not at all, and a chunk of table
sentences is allowed to run past the word target rather than cut one.

**Overlap is prose-only.** Carrying the tail of the previous chunk forward helps
when a sentence of prose straddles a boundary. Repeating a table row would
instead put the same priced benefit in two chunks, which inflates retrieval
scores for whichever copy ranks first and tells the reader nothing new.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from app.rag.parse import ParsedDoc, ParsedPage

logger = logging.getLogger(__name__)

#: Bumped whenever chunking changes shape. The index manifest records it, so a
#: stale index is detected rather than silently queried.
CHUNKER_VERSION = 4

#: Target chunk size in words, and the overlap carried between prose chunks.
TARGET_WORDS = 420
MIN_WORDS = 60
MAX_WORDS = 520
OVERLAP_WORDS = 60

#: A prose chunk below this, after merging, is page furniture rather than
#: content. The first build produced six such chunks -- "Back to glossary" and
#: "Email this page Email" -- which are the navigation of the published page,
#: not the policy on it. They cannot answer a question and they dilute the BM25
#: statistics, so they are dropped and the count is reported.
DROP_BELOW_WORDS = 12

#: Target words for a chunk of table rows, which is much smaller than the prose
#: target. A benefit table page can carry fifteen priced benefits; grouped into
#: one chunk, each is a fifteenth of the text and none is retrievable on its own.
#: See the module docstring note on recall. A row is still never split: this is a
#: target, and a single long row is emitted whole regardless.
TABLE_TARGET_WORDS = 160

#: A Markdown heading emitted by the HTML parser, e.g. "## Internal appeals".
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")

#: A numbered manual heading, e.g. "220.1 - Conditions of Coverage".
_NUMBERED_HEADING = re.compile(r"^\s*\d{1,3}(?:\.\d{1,3}){0,3}\s*[-–—]\s*\S")


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage, with everything needed to cite it.

    Attributes:
        chunk_id: Stable, content-addressed id; also the Chroma document id.
        doc_id: Which knowledge-base document this came from.
        plan_id: The plan it belongs to, or ``ALL`` for a shared document.
        doc_type: sbc, glossary, appeals, coverage_policy, pa_policy or faq.
        title: Document title, shown on a citation chip.
        section: Nearest heading, or the document title when there is none.
        page: Page number in the source document.
        source_file: Repository-relative path of the local copy.
        source_url: Public URL, which the citation chip links to.
        text: The passage itself.
        kind: ``prose`` or ``table`` — a table chunk holds whole row sentences.
        word_count: Words in ``text``.
    """

    chunk_id: str
    doc_id: str
    plan_id: str
    doc_type: str
    title: str
    section: str
    page: int
    source_file: str
    source_url: str
    text: str
    kind: str
    word_count: int

    def metadata(self) -> dict[str, str | int]:
        """Chroma metadata for this chunk. Flat values only, as Chroma requires."""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "plan_id": self.plan_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "section": self.section,
            "page": self.page,
            "source_file": self.source_file,
            "source_url": self.source_url,
            "kind": self.kind,
            "word_count": self.word_count,
        }


@dataclass
class _Block:
    """A run of text under one heading, on one page."""

    section: str
    page: int
    lines: list[str] = field(default_factory=list)
    kind: str = "prose"

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def _is_heading(line: str) -> tuple[bool, str]:
    """Decide whether a line is a heading, and return its text."""
    markdown = _MARKDOWN_HEADING.match(line)
    if markdown:
        return True, markdown.group(2).strip()
    if _NUMBERED_HEADING.match(line) and len(line) < 130:
        return True, line.strip()
    # An all-caps or title-case line short enough to be a heading rather than a
    # sentence. Section titles in the SBC and the manual both look like this.
    stripped = line.strip()
    if 2 <= len(stripped.split()) <= 14 and not stripped.endswith((".", ",", ";", ":")):
        letters = [c for c in stripped if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            return True, stripped
    return False, ""


def _blocks_for_page(page: ParsedPage, *, default_section: str) -> list[_Block]:
    """Split one page into heading-delimited prose blocks plus a table block."""
    blocks: list[_Block] = []
    current = _Block(section=default_section, page=page.page)
    for raw in page.text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading, text = _is_heading(line)
        if heading:
            if current.lines:
                blocks.append(current)
            current = _Block(section=text, page=page.page)
            continue
        current.lines.append(line)
    if current.lines:
        blocks.append(current)

    if page.table_sentences:
        blocks.append(
            _Block(
                section=f"{default_section} — benefit table",
                page=page.page,
                lines=list(page.table_sentences),
                kind="table",
            )
        )
    return blocks


def _chunk_id(doc_id: str, page: int, text: str) -> str:
    """Content-addressed chunk id, stable across rebuilds of the same text."""
    digest = hashlib.sha256(f"{doc_id}|{page}|{text}".encode()).hexdigest()[:12]
    return f"{doc_id}:p{page}:{digest}"


def _tail_words(text: str, count: int) -> str:
    """The last ``count`` words of a passage, for prose overlap."""
    words = text.split()
    return " ".join(words[-count:]) if len(words) > count else text


def chunk_document(
    doc: ParsedDoc,
    *,
    plan_id: str,
    doc_type: str,
    source_file: str,
    source_url: str,
) -> list[Chunk]:
    """Split one parsed document into chunks.

    Args:
        doc: The parsed document.
        plan_id: Real plan id, or ``ALL`` for a shared document.
        doc_type: The catalogue's document type.
        source_file: Repository-relative path of the local copy.
        source_url: Public URL for the citation chip.

    Returns:
        Chunks in document order. Table rows are never split; prose chunks carry
        roughly ``OVERLAP_WORDS`` of the previous chunk's tail.
    """
    chunks: list[Chunk] = []

    def emit(block_section: str, page: int, text: str, kind: str) -> None:
        body = text.strip()
        if not body:
            return
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(doc.doc_id, page, body),
                doc_id=doc.doc_id,
                plan_id=plan_id,
                doc_type=doc_type,
                title=doc.title,
                section=block_section or doc.title,
                page=page,
                source_file=source_file,
                source_url=source_url,
                text=body,
                kind=kind,
                word_count=len(body.split()),
            )
        )

    for page in doc.pages:
        for block in _blocks_for_page(page, default_section=doc.title):
            if block.kind == "table":
                _emit_table_block(block, emit)
            else:
                _emit_prose_block(block, emit)

    merged, dropped = _merge_runts(chunks)
    if dropped:
        logger.debug("%s: dropped %d furniture chunk(s)", doc.doc_id, dropped)
    return merged


def _emit_table_block(block: _Block, emit) -> None:  # type: ignore[no-untyped-def]
    """Group whole table sentences into small chunks, never splitting one.

    The target is deliberately much lower than for prose: a benefit row only
    retrieves if it is a meaningful share of its own chunk. A row longer than the
    target is still emitted whole, because half a priced benefit is worse than an
    oversized chunk.
    """
    batch: list[str] = []
    words = 0
    for sentence in block.lines:
        sentence_words = len(sentence.split())
        if batch and words + sentence_words > TABLE_TARGET_WORDS:
            emit(block.section, block.page, "\n".join(batch), "table")
            batch, words = [], 0
        batch.append(sentence)
        words += sentence_words
    if batch:
        emit(block.section, block.page, "\n".join(batch), "table")


def _emit_prose_block(block: _Block, emit) -> None:  # type: ignore[no-untyped-def]
    """Split a prose block at the word target, carrying overlap forward."""
    batch: list[str] = []
    words = 0
    carried = ""
    for line in block.lines:
        line_words = len(line.split())
        if batch and words + line_words > TARGET_WORDS:
            text = (f"{carried} " if carried else "") + "\n".join(batch)
            emit(block.section, block.page, text, "prose")
            carried = _tail_words("\n".join(batch), OVERLAP_WORDS)
            batch, words = [], 0
        batch.append(line)
        words += line_words
    if batch:
        text = (f"{carried} " if carried else "") + "\n".join(batch)
        emit(block.section, block.page, text, "prose")


def _joined(first: Chunk, second: Chunk) -> Chunk:
    """One chunk from two adjacent ones, keeping the earlier one's section."""
    text = f"{first.text}\n{second.text}"
    return Chunk(
        chunk_id=_chunk_id(first.doc_id, first.page, text),
        doc_id=first.doc_id,
        plan_id=first.plan_id,
        doc_type=first.doc_type,
        title=first.title,
        section=first.section,
        page=first.page,
        source_file=first.source_file,
        source_url=first.source_url,
        text=text,
        kind="prose",
        word_count=len(text.split()),
    )


def _mergeable(first: Chunk, second: Chunk) -> bool:
    """True when two adjacent prose chunks on one page may be joined."""
    return (
        first.kind == "prose"
        and second.kind == "prose"
        and first.doc_id == second.doc_id
        and first.page == second.page
        and first.word_count + second.word_count <= MAX_WORDS
    )


def _merge_runts(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Absorb too-small prose chunks, then drop what is left as furniture.

    Heading-based splitting produces a scatter of one-line chunks: a heading
    with a single sentence under it. On their own they retrieve badly, because
    there is not enough text for either the embedding or BM25 to work with.

    Merging looks both ways. Backwards alone is not enough, because a published
    HTML page opens with its navigation, so the runt is the *first* chunk on the
    page and has nothing behind it to fold into.

    Returns:
        The merged chunks, and how many were dropped as furniture.
    """
    merged: list[Chunk] = []
    for chunk in chunks:
        if merged and chunk.word_count < MIN_WORDS and _mergeable(merged[-1], chunk):
            merged.append(_joined(merged.pop(), chunk))
            continue
        merged.append(chunk)

    # Forward pass, for a runt that had no predecessor to merge into.
    forward: list[Chunk] = []
    index = 0
    while index < len(merged):
        chunk = merged[index]
        following = merged[index + 1] if index + 1 < len(merged) else None
        if (
            chunk.word_count < MIN_WORDS
            and following is not None
            and chunk.word_count >= DROP_BELOW_WORDS
            and _mergeable(chunk, following)
        ):
            forward.append(_joined(chunk, following))
            index += 2
            continue
        forward.append(chunk)
        index += 1

    kept = [c for c in forward if c.kind == "table" or c.word_count >= DROP_BELOW_WORDS]
    return kept, len(forward) - len(kept)
