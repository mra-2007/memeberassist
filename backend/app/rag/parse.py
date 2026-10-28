"""Turn a knowledge-base document into pages of text and table sentences.

Three things here are deliberate, and each was decided by looking at the real
documents rather than by assumption.

**Text comes from PyMuPDF, tables from pdfplumber.** PyMuPDF reads a page's
prose in reading order; pdfplumber is the one that can recover cell boundaries.
Running both over the same page costs little and each is used for what it is
good at.

**Table settings are explicit.** pdfplumber's defaults shred the BCBSTX Summary
of Benefits and Coverage into twelve ragged columns, because that template draws
some rules a few points out of alignment. Snapping and joining at five points
recovers the real five-column grid, and the same settings also read the
Wellpoint template's six columns correctly. Both were checked page by page
before these numbers were written down.

**Every table row becomes a sentence that stands on its own.** A cell reading
"40% coinsurance" is useless once it is separated from its row and its plan, and
a retrieved chunk is always separated from its row and its plan. So each row is
rewritten to name the plan, the common medical event, the service and every
payment column's own header. This is not cosmetic: on the reranker, the terse
form of one real SBC row scored 0.0006 against "Is an MRI covered by my plan?"
while the sentence form scored 0.30 — a 500-fold difference in the signal the
confidence check depends on.

Nothing in this module calls an LLM. Parsing is parsing.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber
import pymupdf
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

#: Recovered by inspecting both issuers' SBC templates page by page. pdfplumber's
#: defaults mis-split the BCBSTX grid; see the module docstring.
TABLE_SETTINGS: dict[str, object] = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 5,
    "join_tolerance": 5,
}

#: Cell text that means "nothing here" in the federal SBC template.
_EMPTY_CELL_MARKERS = frozenset({"", "-", "--", "n/a", "none", "not applicable"})

#: The SBC template writes an empty limitations cell as a row of dashes.
_DASH_PLACEHOLDER = re.compile(r"^-{3,}\s*none\s*-{3,}$", re.IGNORECASE)

#: Header cells that identify a benefit table rather than a layout table.
_BENEFIT_TABLE_MARKERS = (
    "services you may need",
    "common medical event",
    "important questions",
    "what you will pay",
)

#: A numbered manual heading, e.g. "220.1.3 - Certification and Recertification".
_NUMBERED_HEADING = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,3})\s*[-–—]\s*(\S.{0,110})$")

#: Smart punctuation normalised so one word tokenises the same way everywhere.
_PUNCTUATION_MAP = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    " ": " ",
    "​": "",
    "­": "",
    "﻿": "",
}


@dataclass(frozen=True)
class ParsedPage:
    """One page of a document, after text and tables have been separated.

    Attributes:
        page: 1-based page number in the source document.
        text: Prose for this page, with repeated headers and footers removed.
        table_sentences: One self-contained sentence per table row.
        headings: Headings seen on this page, outermost first.
    """

    page: int
    text: str
    table_sentences: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedDoc:
    """A whole document after parsing.

    Attributes:
        doc_id: The catalogue's identifier for this document.
        title: Human title, used as the subject of every table sentence.
        pages: Parsed pages in document order.
        page_offset: Page number of the first parsed page in the original file,
            so a page range still cites the page a reader would turn to.
    """

    doc_id: str
    title: str
    pages: tuple[ParsedPage, ...] = field(default_factory=tuple)
    page_offset: int = 1

    @property
    def table_sentence_count(self) -> int:
        """How many table rows became sentences across the whole document."""
        return sum(len(page.table_sentences) for page in self.pages)

    @property
    def word_count(self) -> int:
        """Total words of prose, excluding table sentences."""
        return sum(len(page.text.split()) for page in self.pages)


# ---------------------------------------------------------------------------
# text normalisation
# ---------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Normalise one string: NFKC, ASCII punctuation, collapsed whitespace."""
    cleaned = unicodedata.normalize("NFKC", text)
    for source, replacement in _PUNCTUATION_MAP.items():
        cleaned = cleaned.replace(source, replacement)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _cell(value: str | None) -> str:
    """Normalise one table cell, flattening its internal line breaks."""
    if value is None:
        return ""
    flattened = re.sub(r"\s*\n\s*", " ", value)
    text = normalise(flattened)
    if _DASH_PLACEHOLDER.match(text):
        return ""
    return "" if text.lower() in _EMPTY_CELL_MARKERS else text


def _is_meaningful(text: str) -> bool:
    """True when a cell carries information worth putting in a sentence."""
    return bool(text) and text.lower() not in _EMPTY_CELL_MARKERS


# ---------------------------------------------------------------------------
# repeated headers and footers
# ---------------------------------------------------------------------------
def repeated_lines(pages: list[str], *, min_pages: int = 3, ratio: float = 0.6) -> set[str]:
    """Find lines that repeat on most pages — a running header or footer.

    A line has to appear on at least ``min_pages`` pages and on at least
    ``ratio`` of them. Short pure-numeric lines are always treated as page
    furniture.
    """
    if len(pages) < min_pages:
        return set()
    counts: Counter[str] = Counter()
    for text in pages:
        counts.update({line.strip() for line in text.splitlines() if line.strip()})
    threshold = max(min_pages, int(len(pages) * ratio))
    return {line for line, count in counts.items() if count >= threshold and len(line) < 200}


def _strip_furniture(text: str, furniture: set[str]) -> str:
    """Drop running headers, footers and bare page numbers from a page."""
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in furniture:
            continue
        if re.fullmatch(r"(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?", line, re.IGNORECASE):
            continue
        if _is_noise_line(line):
            continue
        kept.append(line)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# tables -> self-contained sentences
# ---------------------------------------------------------------------------
def _is_benefit_table(rows: list[list[str | None]]) -> bool:
    """True when a table is a real benefit grid rather than a layout box."""
    if len(rows) < 3:
        return False
    head = " ".join(_cell(c) for row in rows[:2] for c in row).lower()
    return any(marker in head for marker in _BENEFIT_TABLE_MARKERS)


def _header_band(rows: list[list[str | None]]) -> tuple[list[str], int]:
    """Merge the table's header rows into one header per column.

    The SBC template spreads "What You Will Pay" across a second row that names
    each payment column ("Participating Providers (You will pay the least)").
    The lower, more specific label wins; the upper one fills in where the lower
    row is blank.

    Returns:
        The per-column headers and the index of the first data row.
    """
    width = max(len(row) for row in rows)
    headers = [""] * width
    band = 0
    for depth, row in enumerate(rows[:2]):
        cells = [_cell(c) for c in row] + [""] * (width - len(row))
        marker_row = any(m in " ".join(cells).lower() for m in _BENEFIT_TABLE_MARKERS)
        specific_row = depth > 0 and any(cells)
        if not (marker_row or specific_row):
            break
        for index, value in enumerate(cells):
            # A lower row's label is the specific one, so it overwrites.
            if value and (depth > 0 or not headers[index]):
                headers[index] = value
        band = depth + 1
    return headers, band


def _forward_fill(rows: list[list[str]], column: int) -> None:
    """Carry a grouping cell down the rows it governs, in place.

    The SBC prints "If you have a test" once and leaves the cell empty for every
    service in that group, so a row lifted out on its own would lose the event
    it belongs to.
    """
    carried = ""
    for row in rows:
        if column >= len(row):
            continue
        if _is_meaningful(row[column]):
            carried = row[column]
        elif carried:
            row[column] = carried


def table_to_sentences(rows: list[list[str | None]], *, subject: str, page: int) -> list[str]:
    """Rewrite each row of a benefit table as a sentence that stands alone.

    Args:
        rows: The raw cell grid from pdfplumber.
        subject: What the table is about — the plan name for an SBC.
        page: Page number, used only for the log message.

    Returns:
        One sentence per data row. Rows with nothing but a service name are
        skipped rather than turned into a sentence that says nothing.
    """
    if not _is_benefit_table(rows):
        return []

    headers, first_data = _header_band(rows)
    width = len(headers)
    grid = [[_cell(c) for c in row] + [""] * (width - len(row)) for row in rows[first_data:]]
    if not grid:
        return []

    group_column, service_column = _locate_label_columns(headers)
    if group_column is not None:
        _forward_fill(grid, group_column)

    sentences: list[str] = []
    for row in grid:
        sentence = _row_sentence(
            row,
            headers=headers,
            subject=subject,
            group_column=group_column,
            service_column=service_column,
        )
        if sentence:
            sentences.append(sentence)
    logger.debug("page %d: %d table sentences", page, len(sentences))
    return sentences


def _locate_label_columns(headers: list[str]) -> tuple[int | None, int | None]:
    """Find the grouping column and the service column from the headers."""
    group_column: int | None = None
    service_column: int | None = None
    for index, header in enumerate(headers):
        lowered = header.lower()
        if group_column is None and "common medical event" in lowered:
            group_column = index
        elif service_column is None and (
            "services you may need" in lowered or "important questions" in lowered
        ):
            service_column = index
    # The SBC always leads with the event, then the service; fall back to that
    # order when a template words its headers differently.
    if service_column is None:
        service_column = 1 if group_column == 0 and len(headers) > 1 else 0
    return group_column, service_column


def _row_sentence(
    row: list[str],
    *,
    headers: list[str],
    subject: str,
    group_column: int | None,
    service_column: int | None,
) -> str:
    """Build one self-contained sentence from one data row."""
    service = row[service_column] if service_column is not None else ""
    if not _is_meaningful(service):
        return ""

    group = row[group_column] if group_column is not None else ""
    label_columns = {group_column, service_column}
    details = [
        (headers[index] or "What you will pay", value)
        for index, value in enumerate(row)
        if index not in label_columns and _is_meaningful(value) and index < len(headers)
    ]
    if not details:
        return ""

    opening = f"Under the {subject} plan"
    if _is_meaningful(group):
        # The event is phrased as a condition ("If you have a test"), so it
        # reads correctly as a clause rather than a noun.
        opening = f"{opening}, {group[0].lower()}{group[1:]}"
    clauses = "; ".join(f"{header} — {value}" for header, value in details)
    return f"{opening}, for {service} you pay: {clauses}."


# ---------------------------------------------------------------------------
# headings
# ---------------------------------------------------------------------------
def _page_headings(text: str) -> tuple[str, ...]:
    """Headings on a page: numbered manual sections and short title-case lines."""
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 130:
            continue
        numbered = _NUMBERED_HEADING.match(line)
        if numbered:
            found.append(line)
            continue
        words = line.split()
        if 2 <= len(words) <= 16 and not line.endswith((".", ",", ";")):
            letters = [c for c in line if c.isalpha()]
            if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
                found.append(line)
    return tuple(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def parse_pdf(
    data: bytes,
    *,
    doc_id: str,
    title: str,
    subject: str | None = None,
    page_range: tuple[int, int] | None = None,
) -> ParsedDoc:
    """Parse a PDF into pages of prose plus one sentence per table row.

    Args:
        data: The PDF bytes.
        doc_id: Catalogue identifier, carried onto every chunk.
        title: Human title of the document.
        subject: Subject for table sentences; defaults to ``title``.
        page_range: 1-based inclusive page window, or None for the whole file.

    Returns:
        The parsed document, with page numbers as they appear in the original.
    """
    with pymupdf.open(stream=data, filetype="pdf") as document:
        total = document.page_count
        first, last = page_range or (1, total)
        first, last = max(1, first), min(total, last)
        raw_pages = [normalise_page(document[i - 1].get_text()) for i in range(first, last + 1)]

    furniture = repeated_lines(raw_pages)
    if furniture:
        logger.debug("%s: dropping %d repeated header/footer lines", doc_id, len(furniture))

    tables_by_page = _pdf_tables(data, first=first, last=last, subject=subject or title)

    pages = tuple(
        ParsedPage(
            page=number,
            text=truncate_at_references(_strip_furniture(raw, furniture)),
            table_sentences=tuple(tables_by_page.get(number, ())),
            headings=_page_headings(raw),
        )
        for number, raw in enumerate(raw_pages, start=first)
    )
    return ParsedDoc(doc_id=doc_id, title=title, pages=pages, page_offset=first)


def normalise_page(text: str) -> str:
    """Normalise a page's text while keeping its line structure."""
    lines = [normalise(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _pdf_tables(data: bytes, *, first: int, last: int, subject: str) -> dict[int, list[str]]:
    """Extract every benefit table in a page window as sentences, by page."""
    found: dict[int, list[str]] = {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for number in range(first, min(last, len(pdf.pages)) + 1):
            sentences: list[str] = []
            for rows in pdf.pages[number - 1].extract_tables(table_settings=TABLE_SETTINGS):
                sentences.extend(table_to_sentences(rows, subject=subject, page=number))
            if sentences:
                found[number] = sentences
    return found


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
#: Block elements that can hold a run of text. Deliberately includes ``div``:
#: see _leaf_blocks() for the two facts a narrower list lost.
_BLOCK_TAGS = (
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "dt",
    "dd",
    "div",
    "td",
    "th",
    "blockquote",
    "figcaption",
)

#: Headings, which are kept with a Markdown marker so the chunker can split on them.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Lines that are never policy text, whatever document they appear in.
#:
#: Each of these was found polluting the first index build rather than imagined:
#: the Medicare manual stamps a revision line into the middle of its sections,
#: the Aetna bulletin carries a long bibliography, and the HealthCare.gov pages
#: carry their own navigation. A chunk made mostly of these outranked the
#: member's own plan document on a real question, because a bibliography shares
#: vocabulary with every question anyone could ask.
_NOISE_LINE_PATTERNS = (
    # "(Rev. 179, Issued: 01-14-14, Effective: 01-07-14, Implementation: 01-07-14)"
    re.compile(r"^\(?Rev\.\s*\d+", re.IGNORECASE),
    # Page furniture on the published HTML pages.
    re.compile(r"^(back to glossary|email this page.*|print|share|previous|next)$", re.IGNORECASE),
    re.compile(r"^(skip to main content|return to top|was this page helpful\??)$", re.IGNORECASE),
)

#: Text after which a document stops being policy and becomes a bibliography.
_REFERENCES_MARKERS = ("the above policy is based on the following references",)


def _is_noise_line(line: str) -> bool:
    """True when a line is boilerplate rather than policy text."""
    return any(pattern.match(line) for pattern in _NOISE_LINE_PATTERNS)


def truncate_at_references(text: str) -> str:
    """Cut a document where its bibliography begins.

    A clinical policy bulletin ends with hundreds of citations. They are not
    policy, they cannot answer a member's question, and they match almost any
    query because between them they mention every condition in the field. One
    such chunk outranked the member's own plan document on "is a knee MRI
    covered?" in the first index build.
    """
    lowered = text.lower()
    for marker in _REFERENCES_MARKERS:
        position = lowered.find(marker)
        if position > 0:
            return text[:position]
    return text


#: Elements that never carry policy text.
_HTML_NOISE = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "form",
    "noscript",
    "svg",
    "button",
    "iframe",
)


def parse_html(
    html: str,
    *,
    doc_id: str,
    title: str,
    subject: str | None = None,
    selectors: tuple[str, ...] = (),
) -> ParsedDoc:
    """Parse one HTML page into a single parsed page.

    An HTML page has no pagination, so the whole document is page 1 and a
    citation points at the page rather than a page number. Headings are kept as
    lines of their own so the chunker can still split on them.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_HTML_NOISE):
        tag.decompose()

    root = _main_content(soup, selectors)
    lines: list[str] = []
    headings: list[str] = []
    for element in _leaf_blocks(root):
        text = normalise(element.get_text(" ", strip=True))
        if not text or len(text) < 3 or _is_noise_line(text):
            continue
        if element.name in _HEADING_TAGS:
            marker = "#" * int(element.name[1])
            lines.append(f"{marker} {text}")
            headings.append(text)
        else:
            lines.append(text)

    sentences: list[str] = []
    for table in root.find_all("table"):
        rows: list[list[str | None]] = [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        if rows:
            sentences.extend(table_to_sentences(rows, subject=subject or title, page=1))

    page = ParsedPage(
        page=1,
        text=truncate_at_references("\n".join(_dedupe_keep_order(lines))),
        table_sentences=tuple(sentences),
        headings=tuple(dict.fromkeys(headings)),
    )
    return ParsedDoc(doc_id=doc_id, title=title, pages=(page,), page_offset=1)


def _leaf_blocks(root: Tag) -> list[Tag]:
    """Block elements that hold text directly, without a block child.

    Enumerating a whitelist of ``<p>``/``<li>`` is not enough. HealthCare.gov
    renders its explainer paragraphs into bare ``<div>`` elements, so a whitelist
    silently dropped two facts a member is very likely to ask for — the 180-day
    deadline to file an internal appeal and the 45-day standard external-review
    decision. Both were present in the HTML and absent from the index.

    Taking only leaf blocks keeps ``<div>`` content without also emitting every
    ancestor's concatenated text.
    """
    blocks: list[Tag] = []
    for element in root.find_all(_BLOCK_TAGS):
        if not isinstance(element, Tag):
            continue
        if element.find(_BLOCK_TAGS) is not None:
            continue  # an outer container; its children carry the text
        blocks.append(element)
    return blocks


def _main_content(soup: BeautifulSoup, selectors: tuple[str, ...]) -> Tag:
    """Return the first selector that matches real content, else the body.

    A selector is accepted only when it actually contains text: several of the
    published policy pages render their body into a container whose id differs
    between templates, and silently indexing an empty <main> would lose the
    document without failing.
    """
    for selector in selectors:
        for candidate in soup.select(selector):
            if len(candidate.get_text(" ", strip=True)) > 400:
                return candidate
    return soup.body or soup


def _dedupe_keep_order(lines: list[str]) -> list[str]:
    """Drop repeated lines (menus and skip links echo throughout a page)."""
    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        kept.append(line)
    return kept


def parse_markdown(text: str, *, doc_id: str, title: str) -> ParsedDoc:
    """Parse an authored Markdown document (our own FAQ) into one page."""
    lines = [normalise(line) for line in text.splitlines()]
    headings = tuple(line.lstrip("# ").strip() for line in lines if line.startswith("#"))
    page = ParsedPage(page=1, text="\n".join(line for line in lines if line), headings=headings)
    return ParsedDoc(doc_id=doc_id, title=title, pages=(page,), page_offset=1)
