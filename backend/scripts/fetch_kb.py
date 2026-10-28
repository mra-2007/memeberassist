"""Fetch the knowledge-base documents and write ``data/kb/manifest.yaml``.

Run from the repository root::

    uv run python manage.py fetch-kb

What this does, and why it is shaped this way:

* **The manifest is generated, never hand-maintained.** ``retrieved_on`` and the
  SHA-256 of each document are facts about a download that actually happened, so
  they are written by the thing that did the downloading. A manifest edited by
  hand drifts away from the files it describes, and then the provenance claim is
  worth nothing.
* **Plan documents come from the plan records.** The four Summary of Benefits and
  Coverage URLs are the ones ``fetch_plans`` recorded in
  ``data/mappings/selected_plans.csv`` from the CMS filings, and the plan name and
  issuer are read back from the ``plans`` table. So the chain runs federal filing
  → database → knowledge base, with nothing retyped in the middle.
* **A failed document is reported, not skipped quietly.** Each one is attempted,
  the outcome is printed, and the script exits non-zero if any document that the
  index needs is missing.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import logging
import re
import sys
from dataclasses import dataclass

import pymupdf
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR, get_settings
from app.db.models import Plan
from app.db.session import session_scope
from app.external.cache import ExternalApiError, fetch_bytes, fetch_text
from app.rag.kb_sources import SHARED_SOURCES, KbSource, sbc_source
from scripts._common import console, record_source, setup_logging

logger = logging.getLogger(__name__)

KB_DIR = DATA_DIR / "kb"
FILES_DIR = KB_DIR / "files"
AUTHORED_DIR = KB_DIR / "authored"
MANIFEST_PATH = KB_DIR / "manifest.yaml"
SELECTED_PLANS_CSV = DATA_DIR / "mappings" / "selected_plans.csv"

#: Bumped when the manifest's shape changes, so a stale manifest is detectable.
MANIFEST_VERSION = 1


@dataclass
class FetchResult:
    """The outcome of fetching one document."""

    source: KbSource
    ok: bool
    size_bytes: int = 0
    sha256: str = ""
    page_count: int | None = None
    page_range: tuple[int, int] | None = None
    page_range_reason: str | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# plan documents
# ---------------------------------------------------------------------------
def plan_sources(session: Session) -> list[KbSource]:
    """Build a catalogue entry per plan, from the plan records and the CSV.

    Raises:
        FileNotFoundError: if ``fetch_plans`` has not run yet.
    """
    if not SELECTED_PLANS_CSV.exists():
        raise FileNotFoundError(
            f"{SELECTED_PLANS_CSV} is missing — run `manage.py fetch-plans` first."
        )
    with SELECTED_PLANS_CSV.open(encoding="utf-8", newline="") as handle:
        rows = {row["plan_id"]: row for row in csv.DictReader(handle)}

    sources: list[KbSource] = []
    for plan in session.scalars(select(Plan).order_by(Plan.plan_id)):
        row = rows.get(plan.plan_id)
        if row is None or not row.get("sbc_url"):
            console.print(
                f"[yellow]No SBC URL recorded for {plan.plan_id}[/yellow] — "
                "it will be absent from the knowledge base."
            )
            continue
        sources.append(
            sbc_source(
                plan_id=plan.plan_id,
                plan_name=plan.name,
                issuer=plan.issuer,
                sbc_url=row["sbc_url"],
                local_name=f"sbc_{plan.plan_id}.pdf",
            )
        )
    return sources


# ---------------------------------------------------------------------------
# the long manual: find the outpatient-therapy window rather than guess it
# ---------------------------------------------------------------------------
#: Top-level headings in the Benefit Policy Manual, e.g. "220 - Coverage of ...".
_TOP_LEVEL_SECTION = re.compile(
    r"^\s*(\d{2,3})\s*[-–—]\s*\S",  # noqa: RUF001 - the dashes are what is matched
    re.MULTILINE,
)

#: The outpatient-therapy coverage section: 220 up to the start of 230.
#:
#: 220 is "Coverage of Outpatient Rehabilitation Therapy Services" -- conditions
#: of coverage, plans of care, certification, and what counts as reasonable and
#: necessary. 230 is "Practice of Physical Therapy..." -- who may furnish
#: therapy and under whose supervision, which is provider licensing rather than
#: member coverage, and no member question reaches for it.
#:
#: The narrower window is also a retrieval decision. With 220 and 230 together
#: this chapter was 53% of the index and triggered the build's own dominance
#: warning, which is the failure that had already cost us one document.
_THERAPY_FIRST_SECTION = 220
_THERAPY_AFTER_SECTION = 230


def locate_therapy_pages(data: bytes) -> tuple[tuple[int, int], str]:
    """Find the pages of Chapter 15 that hold the outpatient-therapy sections.

    The chapter is 311 pages and only sections 220 and 230 are about outpatient
    therapy, which is what the specification asks for. The window is located by
    reading the manual's own section headings, so a CMS revision that moves the
    sections is followed rather than silently mis-indexed.

    Args:
        data: The chapter's PDF bytes.

    Returns:
        The 1-based inclusive page window and a sentence explaining it.

    Raises:
        ValueError: when section 220 cannot be found in the body at all.
    """
    with pymupdf.open(stream=data, filetype="pdf") as document:
        total = document.page_count
        # A section number appears first in the table of contents and again in
        # the body; the body occurrence is the last one.
        last_page_of: dict[int, int] = {}
        for index in range(total):
            for match in _TOP_LEVEL_SECTION.finditer(document[index].get_text()):
                last_page_of[int(match.group(1))] = index + 1

    start = last_page_of.get(_THERAPY_FIRST_SECTION)
    if start is None:
        raise ValueError(
            "Section 220 was not found in the Benefit Policy Manual chapter; "
            "CMS may have restructured it. Refusing to index a guessed page range."
        )
    after = last_page_of.get(_THERAPY_AFTER_SECTION, total + 1)
    end = max(start, after - 1)
    reason = (
        f"Pages {start}-{end} of {total}: section {_THERAPY_FIRST_SECTION} "
        f"(coverage of outpatient rehabilitation therapy services) up to the page before "
        f"section {_THERAPY_AFTER_SECTION} (practice of physical therapy, which is provider "
        f"licensing rather than member coverage). Located from the chapter's own headings."
    )
    return (start, end), reason


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def fetch_one(source: KbSource) -> FetchResult:
    """Fetch one document to ``data/kb/files`` and describe what arrived."""
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    target = FILES_DIR / source.local_name

    try:
        if source.kind == "authored":
            origin = AUTHORED_DIR / source.local_name
            if not origin.exists():
                return FetchResult(source, ok=False, error=f"authored file missing: {origin}")
            payload = origin.read_bytes()
        elif source.kind == "pdf":
            payload = fetch_bytes(source.source_url, cache_to_raw=False)
            if not payload.startswith(b"%PDF"):
                return FetchResult(
                    source,
                    ok=False,
                    error=f"not a PDF (starts with {payload[:12]!r}) — the URL may now serve a page",
                )
        else:
            payload = fetch_text(source.source_url).encode("utf-8")
            # Checked over the whole document rather than a leading window: the
            # Aetna policy page opens with more than 2 KB of whitespace before
            # its first tag, which a prefix check wrongly rejects.
            if b"<" not in payload or b"</" not in payload:
                return FetchResult(source, ok=False, error="response does not look like HTML")
    except (ExternalApiError, OSError) as error:
        return FetchResult(source, ok=False, error=f"{type(error).__name__}: {error}")

    target.write_bytes(payload)
    result = FetchResult(
        source=source,
        ok=True,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    if source.kind == "pdf":
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            result.page_count = document.page_count
        if source.page_range is not None:
            window, reason = locate_therapy_pages(payload)
            result.page_range = window
            result.page_range_reason = reason
    return result


def manifest_entry(result: FetchResult, *, retrieved_on: dt.date) -> dict[str, object]:
    """One manifest record for a fetched document."""
    source = result.source
    entry: dict[str, object] = {
        "doc_id": source.doc_id,
        "title": source.title,
        "doc_type": source.doc_type,
        "plan_id": source.plan_id,
        "publisher": source.publisher,
        "source_url": source.source_url,
        "retrieved_on": retrieved_on.isoformat(),
        "licence_note": source.licence_note,
        "generated": source.kind == "authored",
        "local_file": f"kb/files/{source.local_name}",
        "format": source.kind,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
    }
    if result.page_count is not None:
        entry["page_count"] = result.page_count
    if result.page_range is not None:
        entry["page_range"] = list(result.page_range)
        entry["page_range_reason"] = result.page_range_reason
    if source.html_selectors:
        entry["html_selectors"] = list(source.html_selectors)
    return entry


def write_manifest(results: list[FetchResult], *, retrieved_on: dt.date) -> dict[str, object]:
    """Write ``data/kb/manifest.yaml`` describing every fetched document."""
    documents = [manifest_entry(r, retrieved_on=retrieved_on) for r in results if r.ok]
    manifest: dict[str, object] = {
        "manifest_version": MANIFEST_VERSION,
        "generated_by": "backend/scripts/fetch_kb.py",
        "generated_on": retrieved_on.isoformat(),
        "note": (
            "Generated file — do not edit by hand. Re-run `manage.py fetch-kb` to refresh it. "
            "Every entry's sha256 is of the bytes stored under data/kb/files, which is "
            "git-ignored: the manifest is the committed record of what was fetched."
        ),
        "document_count": len(documents),
        "documents": documents,
    }
    KB_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Fetch every knowledge-base document and write the manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    settings = get_settings()
    console.print(
        f"[bold]Fetching the knowledge base[/bold] "
        f"(plan year {settings.plan_year}, {settings.plan_state})"
    )

    with session_scope() as session:
        sources = plan_sources(session) + list(SHARED_SOURCES)

        results = [fetch_one(source) for source in sources]
        for result in results:
            source = result.source
            if result.ok:
                pages = f"{result.page_count}p" if result.page_count else "html"
                window = (
                    f" -> pages {result.page_range[0]}-{result.page_range[1]}"
                    if result.page_range
                    else ""
                )
                console.print(
                    f"  [green]ok[/green]   {source.doc_id:34s} {source.plan_id:16s} "
                    f"{result.size_bytes:>9,}B {pages}{window}"
                )
            else:
                console.print(
                    f"  [red]FAIL[/red] {source.doc_id:34s} {source.plan_id:16s} {result.error}"
                )

        failures = [r for r in results if not r.ok]
        retrieved_on = dt.date.today()
        manifest = write_manifest(results, retrieved_on=retrieved_on)

        ok_count = len(results) - len(failures)
        record_source(
            session,
            key="kb_documents",
            name="MemberAssist knowledge base (plan SBCs, federal glossaries and policy, one commercial PA policy)",
            # One row stands for a set of documents, so it points at the manifest
            # that records each document's own source_url, licence and hash.
            url="https://github.com/mra-2007/memeberassist/blob/main/data/kb/manifest.yaml",
            licence_note=(
                "Mixed: U.S. federal government works (public domain) plus issuer and insurer "
                "documents quoted and cited, not redistributed. Per-document notes in the manifest."
            ),
            row_count=ok_count,
            notes=f"manifest_version {MANIFEST_VERSION}; {ok_count} documents fetched",
            retrieved_on=retrieved_on,
        )

    console.print(
        f"\n[bold]{ok_count}[/bold] documents in {MANIFEST_PATH.relative_to(DATA_DIR.parent)}"
        f"  ({manifest['document_count']} recorded)"
    )
    if failures:
        console.print(
            f"[red]{len(failures)} document(s) could not be fetched.[/red] "
            "The manifest records only what actually arrived."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
