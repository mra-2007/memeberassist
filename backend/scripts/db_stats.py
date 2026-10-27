"""Print the row count of every table, plus the recorded data sources.

uv run python manage.py db-stats
"""

from __future__ import annotations

from rich.table import Table
from sqlalchemy import select

from app.config import get_settings
from app.db.models import DataSource
from app.db.session import session_scope
from scripts._common import console, print_row_counts


def main() -> None:
    settings = get_settings()
    backend = "SQLite (temporary fallback)" if settings.is_sqlite else "PostgreSQL"
    console.print(f"[bold]Database:[/bold] {backend}")

    with session_scope() as session:
        print_row_counts(session)

        sources = list(session.scalars(select(DataSource).order_by(DataSource.key)))
        if not sources:
            console.print("[yellow]No data sources recorded yet.[/yellow]")
            return
        table = Table(title="Recorded data sources")
        table.add_column("Key", style="cyan")
        table.add_column("Name", overflow="fold")
        table.add_column("Retrieved")
        table.add_column("Rows", justify="right")
        for source in sources:
            table.add_row(
                source.key, source.name, source.retrieved_on.isoformat(), f"{source.row_count:,}"
            )
        console.print(table)


if __name__ == "__main__":
    main()
