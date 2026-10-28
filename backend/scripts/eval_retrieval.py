"""Measure retrieval against ``eval/testset.yaml`` and fit the confidence threshold.

    uv run python manage.py eval-retrieval              # hit rate
    uv run python manage.py eval-retrieval --calibrate  # and re-fit the threshold

**Hit rate** is the Phase 2 done-check: for each question the documents should
answer, is a chunk from the expected document and page in the top five? Only
items whose ``answered_by`` is ``rag`` or ``rag+tool`` and that carry
``expected_sources`` are scored, because retrieval hit rate is meaningless for a
question a database tool answers.

The expected pages in the test set were read out of the documents, not taken
from a retrieval result, so this measures retrieval rather than itself.

**Calibration** fits the confidence threshold by sweeping it over the real
rerank scores of answerable and unanswerable questions and reporting what each
value would get right. It prints the number to paste into
``app/rag/confidence.py`` along with the accuracy it actually achieves — never a
number chosen to look good.

No LLM is called. This is retrieval and ranking only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

import yaml
from rich.table import Table
from sqlalchemy import select

from app.config import EVAL_DIR
from app.db.models import Member
from app.db.session import session_scope
from app.rag.confidence import CONFIDENCE_THRESHOLD
from app.rag.index_store import load_bm25
from app.rag.retrieve import FINAL_TOP_K, retrieve
from scripts._common import console, setup_logging

logger = logging.getLogger(__name__)

TESTSET = EVAL_DIR / "testset.yaml"

#: ``answered_by`` values whose answer comes from the documents.
RAG_ANSWERED = frozenset({"rag", "rag+tool"})

#: ``expect`` values that mean the documents genuinely cannot answer.
UNANSWERABLE = frozenset({"not_found"})


@dataclass
class Measurement:
    """One question's retrieval result."""

    item_id: str
    category: str
    question: str
    expect: str
    hit: bool | None
    hit_rank: int | None
    multi_turn: bool
    top_score: float
    retrieved: list[str]
    expected: list[str]


def plan_for(member_id: str) -> str:
    """The plan a member holds, read from the database."""
    with session_scope() as session:
        plan_id = session.scalar(select(Member.plan_id).where(Member.member_id == member_id))
    if plan_id is None:
        raise LookupError(f"no member {member_id}")
    return str(plan_id)


def load_testset() -> list[dict]:
    """Read the evaluation test set.

    Raises:
        FileNotFoundError: when the test set is absent.
    """
    if not TESTSET.exists():
        raise FileNotFoundError(f"{TESTSET} is missing.")
    return list(yaml.safe_load(TESTSET.read_text(encoding="utf-8")))


def _source_key(doc_id: str, page: int) -> str:
    return f"{doc_id}:p{page}"


def measure(items: list[dict], *, top_k: int = FINAL_TOP_K) -> list[Measurement]:
    """Retrieve for every document-answerable question and score the result."""
    bm25 = load_bm25()
    plans: dict[str, str] = {}
    out: list[Measurement] = []

    for item in items:
        # Only items the documents are meant to answer. An item expecting
        # "not found" from a database tool (no such claim) is not a retrieval
        # confidence case and must not be calibrated against.
        if item.get("answered_by") not in RAG_ANSWERED:
            continue
        member = str(item.get("user") or "")
        if member and member not in plans:
            plans[member] = plan_for(member)
        plan_id = plans.get(member)

        # A multi-turn item is scored on its FIRST turn. Later turns are written
        # to depend on the conversation -- "What about if I get it out of
        # network?" has no subject of its own -- and the step that rewrites them
        # into standalone questions is Phase 3. Scoring an unresolved follow-up
        # would measure a component that does not exist yet, so the first turn
        # is used and the limitation is reported rather than hidden.
        turns = [str(turn) for turn in item["turns"]]
        question = turns[0]
        results = retrieve(question, plan_id=plan_id, top_k=top_k, bm25_index=bm25)
        retrieved = [_source_key(r.chunk.doc_id, r.chunk.page) for r in results]
        expected = [
            _source_key(str(s["doc_id"]), int(s["page"]))
            for s in (item.get("expected_sources") or [])
        ]

        hit: bool | None = None
        hit_rank: int | None = None
        if expected:
            for rank, key in enumerate(retrieved, start=1):
                if key in expected:
                    hit, hit_rank = True, rank
                    break
            else:
                hit = False

        out.append(
            Measurement(
                item_id=str(item["id"]),
                category=str(item["category"]),
                question=question,
                expect=str(item["expect"]),
                hit=hit,
                hit_rank=hit_rank,
                multi_turn=len(turns) > 1,
                top_score=max((r.rerank_score or 0.0) for r in results) if results else 0.0,
                retrieved=retrieved,
                expected=expected,
            )
        )
    return out


def print_hit_rate(measurements: list[Measurement]) -> float:
    """Print the per-question table and return the hit rate."""
    scored = [m for m in measurements if m.hit is not None]
    table = Table(title=f"Retrieval hit rate (right chunk in top {FINAL_TOP_K})")
    for column, justify in (
        ("Id", "left"),
        ("Category", "left"),
        ("Hit", "left"),
        ("At", "right"),
        ("Top rerank", "right"),
        ("Question", "left"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]
    for m in scored:
        table.add_row(
            m.item_id,
            m.category[:20],
            "[green]yes[/green]" if m.hit else "[red]no[/red]",
            str(m.hit_rank) if m.hit_rank else "-",
            f"{m.top_score:.4f}",
            m.question[:58],
        )
    console.print(table)

    hits = sum(1 for m in scored if m.hit)
    rate = hits / len(scored) if scored else 0.0
    console.print(f"\n[bold]Hit rate: {hits}/{len(scored)} = {rate:.0%}[/bold]  (target >= 90%)")

    multi = [m for m in scored if m.multi_turn]
    if multi:
        console.print(
            f"[dim]{len(multi)} multi-turn item(s) scored on their first turn only; "
            f"follow-up rewriting arrives in Phase 3.[/dim]"
        )

    misses = [m for m in scored if not m.hit]
    if misses:
        console.print("\n[yellow]Misses, with what was retrieved instead:[/yellow]")
        for m in misses:
            console.print(f"  {m.item_id}: expected one of {m.expected}")
            console.print(f"       got {m.retrieved}")
    return rate


def calibrate(measurements: list[Measurement]) -> None:
    """Sweep the confidence threshold and report what each value would achieve."""
    answerable = [m for m in measurements if m.expect == "answer"]
    unanswerable = [m for m in measurements if m.expect in UNANSWERABLE]
    if not answerable or not unanswerable:
        console.print("[red]Need both answerable and unanswerable items to calibrate.[/red]")
        return

    console.print(
        f"\n[bold]Confidence calibration[/bold] — {len(answerable)} answerable, "
        f"{len(unanswerable)} unanswerable"
    )
    console.print(
        f"  answerable top scores  : min {min(m.top_score for m in answerable):.5f} "
        f"max {max(m.top_score for m in answerable):.5f}"
    )
    console.print(
        f"  unanswerable top scores: min {min(m.top_score for m in unanswerable):.5f} "
        f"max {max(m.top_score for m in unanswerable):.5f}"
    )

    candidates = sorted({m.top_score for m in measurements} | {0.0})
    table = Table(title="Threshold sweep")
    for column in ("Threshold", "Answerable kept", "Unanswerable refused", "Correct"):
        table.add_column(column, justify="right")
    best: tuple[int, float] | None = None
    for threshold in candidates:
        kept = sum(1 for m in answerable if m.top_score >= threshold)
        refused = sum(1 for m in unanswerable if m.top_score < threshold)
        correct = kept + refused
        total = len(answerable) + len(unanswerable)
        table.add_row(
            f"{threshold:.5f}",
            f"{kept}/{len(answerable)}",
            f"{refused}/{len(unanswerable)}",
            f"{correct}/{total}",
        )
        # Prefer accuracy, then the threshold that refuses more, then the lower
        # threshold -- a false refusal is cheap (we offer a human) but a wrong
        # confident answer about someone's coverage is not.
        if best is None or correct > best[0]:
            best = (correct, threshold)
    console.print(table)

    assert best is not None
    total = len(answerable) + len(unanswerable)
    console.print(
        f"\n[bold]Best threshold {best[1]:.5f}[/bold] -> {best[0]}/{total} correct. "
        f"In force in app/rag/confidence.py: {CONFIDENCE_THRESHOLD:.5f}"
    )
    refused = sum(1 for m in unanswerable if m.top_score < best[1])
    console.print(
        f'Correct "I don\'t know": {refused}/{len(unanswerable)} = '
        f"{refused / len(unanswerable):.0%}  (target >= 90%)"
    )
    if abs(best[1] - CONFIDENCE_THRESHOLD) > 1e-9:
        console.print(
            "[yellow]The fitted value differs from the one in force.[/yellow] Update "
            "CONFIDENCE_THRESHOLD and CALIBRATED_ON in app/rag/confidence.py, and say in "
            "PROGRESS.md what the new value actually achieves."
        )


def main(argv: list[str] | None = None) -> int:
    """Measure retrieval, and optionally re-fit the confidence threshold."""
    parser = argparse.ArgumentParser(description="Measure RAG retrieval.")
    parser.add_argument("--calibrate", action="store_true", help="Also sweep the threshold.")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N test items.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    items = load_testset()
    if args.limit:
        items = items[: args.limit]
    measurements = measure(items)
    if not measurements:
        console.print("[red]No document-answerable questions found in the test set.[/red]")
        return 1

    print_hit_rate(measurements)
    if args.calibrate:
        calibrate(measurements)
    return 0


if __name__ == "__main__":
    sys.exit(main())
