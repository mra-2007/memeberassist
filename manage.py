#!/usr/bin/env python
"""MemberAssist task runner.

Cross-platform replacement for a Makefile (the team uses Windows and macOS).
Run every command from the repository root:

    uv run python manage.py --help

Commands are added by the phase that makes them real; nothing here prints a
success message for work it did not actually do.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import typer
from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent
BACKEND = REPO_ROOT / "backend"
FRONTEND = REPO_ROOT / "frontend"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="MemberAssist development and operations commands.",
)
console = Console()


def _run(cmd: list[str], cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> int:
    """Run a subprocess, echoing the command first. Returns its exit code."""
    env = {**os.environ, **(env_extra or {})}
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    # On Windows, npm/npx are .cmd shims that subprocess cannot resolve from a
    # bare name, so look the executable up before spawning it.
    executable = shutil.which(cmd[0])
    if executable is None:
        console.print(f"[red]Not found:[/red] {cmd[0]} — is it installed and on PATH?")
        return 127
    try:
        return subprocess.run(
            [executable, *cmd[1:]], cwd=cwd or REPO_ROOT, env=env, check=False
        ).returncode
    except OSError as error:
        console.print(f"[red]Could not run {cmd[0]}:[/red] {error}")
        return 127


def _exit(code: int) -> None:
    """Propagate a subprocess exit code as our own."""
    if code != 0:
        raise typer.Exit(code)


def _tool_version(name: str, args: list[str]) -> str:
    """Return a tool's version string, or 'not installed'."""
    exe = shutil.which(name)
    if not exe:
        return "[red]not installed[/red]"
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "[yellow]present, version unreadable[/yellow]"
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0] if text else "[yellow]present, version unreadable[/yellow]"


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------
@app.command()
def env_report() -> None:
    """Print the machine and toolchain report (Phase 0 done-check)."""
    import ctypes

    table = Table(title="MemberAssist environment", show_lines=False)
    table.add_column("Item", style="cyan", no_wrap=True)
    table.add_column("Value")

    table.add_row("OS", f"{platform.system()} {platform.release()} ({platform.machine()})")
    table.add_row("Python (running)", sys.version.split()[0])
    table.add_row("CPU", platform.processor() or "unknown")
    table.add_row("CPU cores", str(os.cpu_count()))

    if platform.system() == "Windows":

        class MemoryStatusEx(ctypes.Structure):
            _fields_: ClassVar[list[tuple[str, Any]]] = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
        table.add_row("RAM total", f"{stat.ullTotalPhys / 1024**3:.1f} GB")
        table.add_row("RAM available", f"{stat.ullAvailPhys / 1024**3:.1f} GB")
    else:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        table.add_row("RAM total", f"{pages * page_size / 1024**3:.1f} GB")

    usage = shutil.disk_usage(REPO_ROOT)
    table.add_row("Disk free (repo drive)", f"{usage.free / 1024**3:.1f} GB")

    table.add_section()
    for name, args in [
        ("git", ["--version"]),
        ("uv", ["--version"]),
        ("node", ["--version"]),
        ("npm", ["--version"]),
        ("docker", ["--version"]),
        ("ollama", ["--version"]),
        ("java", ["-version"]),
    ]:
        table.add_row(name, _tool_version(name, args))

    console.print(table)
    console.print(
        "[dim]Need: >= 8 GB RAM (16 GB for Ollama) and >= 15 GB free disk. "
        "Node >= 20. Docker for PostgreSQL.[/dim]"
    )


# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------
@app.command()
def lint(
    fix: bool = typer.Option(False, "--fix", help="Apply ruff autofixes and format."),
) -> None:
    """Run ruff on the backend (and ESLint on the frontend when it exists)."""
    if fix:
        _exit(_run(["uv", "run", "ruff", "check", "--fix", "."], cwd=BACKEND))
        _exit(_run(["uv", "run", "ruff", "format", "."], cwd=BACKEND))
    else:
        _exit(_run(["uv", "run", "ruff", "check", "."], cwd=BACKEND))
        _exit(_run(["uv", "run", "ruff", "format", "--check", "."], cwd=BACKEND))
    if (FRONTEND / "package.json").exists():
        _exit(_run(["npm", "run", "lint"], cwd=FRONTEND))


@app.command()
def typecheck() -> None:
    """Run mypy over the gateway and tools packages."""
    _exit(_run(["uv", "run", "mypy"], cwd=BACKEND))


@app.command()
def test(
    live: bool = typer.Option(False, "--live", help="Include tests that call real APIs/models."),
) -> None:
    """Run the fast test suite (the live and slow markers are excluded by default)."""
    cmd = ["uv", "run", "pytest"]
    if not live:
        cmd += ["-m", "not live and not slow"]
    _exit(_run(cmd, cwd=BACKEND))


# ---------------------------------------------------------------------------
# running the app
# ---------------------------------------------------------------------------
@app.command()
def api(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = typer.Option(True, help="Auto-reload on code changes."),
) -> None:
    """Start the FastAPI backend with uvicorn."""
    cmd = ["uv", "run", "uvicorn", "app.main:app", "--host", host, "--port", str(port)]
    if reload:
        cmd.append("--reload")
    _exit(_run(cmd, cwd=BACKEND))


@app.command()
def web() -> None:
    """Start the Vite frontend dev server."""
    if not (FRONTEND / "package.json").exists():
        console.print("[red]frontend/package.json is missing.[/red]")
        raise typer.Exit(1)
    _exit(_run(["npm", "run", "dev"], cwd=FRONTEND))


@app.command()
def db_up() -> None:
    """Start the PostgreSQL container used for development."""
    _exit(_run(["docker", "compose", "up", "-d", "db"]))


@app.command()
def db_wait(timeout: int = 60) -> None:
    """Block until the development database accepts TCP connections."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(2.0)
            if sock.connect_ex(("127.0.0.1", 5432)) == 0:
                console.print("[green]PostgreSQL is accepting connections.[/green]")
                return
        time.sleep(1.0)
    console.print(f"[red]PostgreSQL did not become ready within {timeout}s.[/red]")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# database schema
# ---------------------------------------------------------------------------
@app.command()
def migrate(revision: str = "head") -> None:
    """Apply Alembic migrations (default: upgrade to head)."""
    _exit(_run(["uv", "run", "alembic", "upgrade", revision], cwd=BACKEND))


@app.command()
def db_revision(message: str) -> None:
    """Autogenerate a new Alembic migration from the models."""
    _exit(_run(["uv", "run", "alembic", "revision", "--autogenerate", "-m", message], cwd=BACKEND))


@app.command()
def db_stats() -> None:
    """Print the row count of every table."""
    _exit(_run(["uv", "run", "python", "-m", "scripts.db_stats"], cwd=BACKEND))


# ---------------------------------------------------------------------------
# data pipeline (run in this order)
# ---------------------------------------------------------------------------
def _script(module: str, extra: list[str] | None = None) -> None:
    """Run one pipeline script as a module so package imports resolve."""
    _exit(_run(["uv", "run", "python", "-m", f"scripts.{module}", *(extra or [])], cwd=BACKEND))


@app.command()
def fetch_plans(
    year: int = typer.Option(None, help="Plan year (default: PLAN_YEAR from .env)."),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Load real Marketplace plans and cost sharing from the CMS PUFs."""
    extra = (["--year", str(year)] if year else []) + (["-v"] if verbose else [])
    _script("fetch_plans", extra)


@app.command()
def fetch_prices(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Load Medicare benchmark prices from the CMS Physician Fee Schedule."""
    _script("fetch_prices", ["-v"] if verbose else [])


@app.command()
def fetch_providers(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Load real providers for the chosen city from the NPI Registry."""
    _script("fetch_providers", ["-v"] if verbose else [])


@app.command()
def verify_mappings(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Verify every ICD-10 code in the mappings against NLM Clinical Tables."""
    _script("verify_mappings", ["-v"] if verbose else [])


@app.command()
def make_data(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Build the synthetic members, claims, accumulators and demo scenario."""
    _script("make_synthetic_data", ["-v"] if verbose else [])


@app.command()
def demo_facts() -> None:
    """Print the demo scenario's real numbers (plan, accumulators, claims)."""
    _script("demo_facts")


@app.command()
def seed() -> None:
    """Run the whole data pipeline in order, from empty database to demo-ready."""
    migrate()
    fetch_plans(year=None, verbose=False)
    fetch_prices(verbose=False)
    fetch_providers(verbose=False)
    verify_mappings(verbose=False)
    make_data(verbose=False)
    db_stats()


if __name__ == "__main__":
    app()
