"""Query the index from the command line and show every score.

    uv run python manage.py ask-index "Is a knee MRI covered?" --plan 47501TX0040002
    uv run python manage.py ask-index "What is a deductible?" --member M10234

This is the Phase 2 done-check and a debugging tool, not part of the serving
path. It prints the vector score and rank, the BM25 score and rank, the fused
score and the rerank score for each chunk, so a bad answer can be traced to the
stage that caused it.

Passing ``--member`` looks the member's plan up in the database, which is how
the gateway will do it: the plan comes from the verified session, never from the
question text.

No LLM is called here. This is retrieval only — the generation step arrives in
Phase 3.
"""

from __future__ import annotations

import argparse
import sys

from rich.table import Table
from sqlalchemy import select

from app.db.models import Member
from app.db.session import session_scope
from app.rag.confidence import confidence_verdict
from app.rag.retrieve import retrieve
from scripts._common import console, setup_logging


def plan_for_member(member_id: str) -> str:
    """Look up a member's plan id.

    Raises:
        LookupError: when no such member exists.
    """
    with session_scope() as session:
        plan_id = session.scalar(select(Member.plan_id).where(Member.member_id == member_id))
    if plan_id is None:
        raise LookupError(f"no member {member_id}")
    return str(plan_id)


def main(argv: list[str] | None = None) -> int:
    """Run one query against the index and print the ranked chunks."""
    parser = argparse.ArgumentParser(description="Query the RAG index.")
    parser.add_argument("question", help="The question to retrieve for.")
    parser.add_argument("--plan", default=None, help="Plan id to filter to.")
    parser.add_argument("--member", default=None, help="Member id; its plan is looked up.")
    parser.add_argument("--top", type=int, default=5, help="Chunks to keep after reranking.")
    parser.add_argument("--full", action="store_true", help="Print each chunk in full.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    plan_id = args.plan
    if args.member:
        plan_id = plan_for_member(args.member)
        console.print(f"[dim]member {args.member} -> plan {plan_id}[/dim]")
    if plan_id is None:
        console.print(
            "[yellow]No plan given, so only shared documents are searchable.[/yellow] "
            "Pass --plan or --member to include a plan's own documents."
        )

    results = retrieve(args.question, plan_id=plan_id, top_k=args.top)
    console.print(f"\n[bold]{args.question}[/bold]  (plan filter: {plan_id or 'ALL only'})")

    if not results:
        console.print("[red]Nothing retrieved.[/red]")
        return 0

    table = Table(show_lines=False)
    for column, justify in (
        ("#", "right"),
        ("Document", "left"),
        ("Pg", "right"),
        ("Kind", "left"),
        ("Vector", "right"),
        ("BM25", "right"),
        ("RRF", "right"),
        ("Rerank", "right"),
        ("Found by", "left"),
    ):
        table.add_column(column, justify=justify)  # type: ignore[arg-type]

    for position, result in enumerate(results, start=1):
        table.add_row(
            str(position),
            result.chunk.doc_id,
            str(result.chunk.page),
            result.chunk.kind,
            _score(result.vector_score, result.vector_rank),
            _score(result.bm25_score, result.bm25_rank),
            f"{result.rrf_score:.5f}",
            f"{result.rerank_score:.4f}" if result.rerank_score is not None else "-",
            result.found_by,
        )
    console.print(table)

    verdict = confidence_verdict(results)
    colour = "green" if verdict.answerable else "yellow"
    console.print(f"[{colour}]{verdict.explain()}[/{colour}]\n")

    for position, result in enumerate(results, start=1):
        chunk = result.chunk
        console.print(
            f"[bold][S{position}][/bold] {chunk.title} — {chunk.section} (page {chunk.page})"
        )
        body = chunk.text if args.full else chunk.text[:400]
        console.print(f"[dim]{body}{'' if args.full else ' …'}[/dim]\n")
    return 0


def _score(value: float | None, rank: int | None) -> str:
    """Format one search's score and rank, or a dash when it did not find it."""
    if value is None or rank is None:
        return "-"
    return f"{value:.4f} (#{rank})"


if __name__ == "__main__":
    sys.exit(main())
