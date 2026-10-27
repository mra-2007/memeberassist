"""Shared helpers for the data-pipeline scripts.

Every script here is reproducible: same inputs, same database rows. They print
what they did and record their source in the ``data_sources`` table so the
provenance claim in ``docs/data_sources.md`` is checkable rather than asserted.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable
from decimal import Decimal

from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Base, DataSource

console = Console()

#: Deterministic seed for every random choice in the data pipeline, so the
#: demo scenario is identical on every machine.
RANDOM_SEED = 20261027


def setup_logging(verbose: bool = False) -> None:
    """Quiet by default; the scripts narrate through ``console`` instead."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-5s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def record_source(
    session: Session,
    *,
    key: str,
    name: str,
    url: str,
    licence_note: str,
    row_count: int,
    notes: str | None = None,
    retrieved_on: dt.date | None = None,
) -> None:
    """Insert or update one row in ``data_sources``."""
    existing = session.scalar(select(DataSource).where(DataSource.key == key))
    retrieved = retrieved_on or dt.date.today()
    if existing is None:
        session.add(
            DataSource(
                key=key,
                name=name,
                url=url,
                licence_note=licence_note,
                retrieved_on=retrieved,
                row_count=row_count,
                notes=notes,
            )
        )
        return
    existing.name = name
    existing.url = url
    existing.licence_note = licence_note
    existing.retrieved_on = retrieved
    existing.row_count = row_count
    existing.notes = notes


def row_counts(
    session: Session, tables: Iterable[type[Base]] | None = None
) -> list[tuple[str, int]]:
    """Row count per table, for the end-of-script summary."""
    models = (
        list(tables)
        if tables is not None
        else sorted(
            (mapper.class_ for mapper in Base.registry.mappers),
            key=lambda model: model.__tablename__,
        )
    )
    out: list[tuple[str, int]] = []
    for model in models:
        count = session.scalar(select(func.count()).select_from(model)) or 0
        out.append((model.__tablename__, count))
    return out


def print_row_counts(session: Session, title: str = "Row counts") -> None:
    """Print every table's row count as a table."""
    table = Table(title=title)
    table.add_column("Table", style="cyan")
    table.add_column("Rows", justify="right")
    for name, count in row_counts(session):
        table.add_row(name, f"{count:,}")
    console.print(table)


def money(value: Decimal | float | int | None) -> str:
    """Format money for console output."""
    if value is None:
        return "-"
    return f"${Decimal(str(value)):,.2f}"


def percent(value: Decimal | float | None) -> str:
    """Format a stored coinsurance fraction as a percentage."""
    if value is None:
        return "-"
    return f"{Decimal(str(value)) * 100:.0f}%"
