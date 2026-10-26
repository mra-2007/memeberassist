"""Enforces the architecture rule behind security invariant 2.2.1.

Only ``app/gateway/llm_router.py`` may import an LLM SDK or name an LLM HTTP
endpoint. Everything else -- tools, RAG, services, API routes and scripts --
must go through the router, so masking, the tool allowlist, the output guard
and the audit log can never be bypassed.

The single documented exception is ``scripts/run_eval.py``, where the RAGAS
judge is configured; the judge only ever receives masked text taken from the
pipeline trace.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_ROOT / "app"
SCRIPTS_DIR = BACKEND_ROOT / "scripts"

# Dotted module names of every SDK that can talk to a language model.
FORBIDDEN_MODULES = frozenset(
    {
        "google.genai",
        "google.generativeai",
        "openai",
        "groq",
        "ollama",
        "anthropic",
        "litellm",
        "langchain",
        "langchain_openai",
        "langchain_google_genai",
    }
)

# Paths (relative to backend/) allowed to import those modules.
ALLOWED_PATHS = frozenset(
    {
        "app/gateway/llm_router.py",
        "scripts/run_eval.py",  # RAGAS judge set-up; receives masked text only
    }
)

# Host names of LLM inference endpoints. No module may hard-code a call to one;
# the router owns every base URL and reads them from configuration.
FORBIDDEN_ENDPOINT_HOSTS = (
    "generativelanguage.googleapis.com",
    "api.openai.com",
    "api.groq.com",
    "openrouter.ai",
    "api.anthropic.com",
)


def _python_files() -> list[Path]:
    """Every backend Python file that the rule applies to."""
    files: list[Path] = []
    for root in (APP_DIR, SCRIPTS_DIR):
        if root.exists():
            files.extend(
                path for path in root.rglob("*.py") if "migrations/versions" not in path.as_posix()
            )
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _imported_modules(tree: ast.AST) -> set[str]:
    """Dotted module names imported anywhere in a parsed file."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _violates(module: str) -> bool:
    """True when a dotted import name is, or sits under, a forbidden module."""
    return any(module == bad or module.startswith(f"{bad}.") for bad in FORBIDDEN_MODULES)


def test_only_the_llm_router_imports_an_llm_sdk() -> None:
    """No module outside the allowlist may import an LLM SDK."""
    offenders: list[str] = []
    for path in _python_files():
        relative = _relative(path)
        if relative in ALLOWED_PATHS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = sorted(name for name in _imported_modules(tree) if _violates(name))
        if bad:
            offenders.append(f"{relative}: {', '.join(bad)}")
    assert not offenders, "LLM SDK imported outside the router:\n" + "\n".join(offenders)


def test_no_module_hard_codes_an_llm_endpoint() -> None:
    """LLM base URLs belong to the router's configuration, nowhere else."""
    offenders: list[str] = []
    for path in _python_files():
        relative = _relative(path)
        if relative in ALLOWED_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        hits = sorted({host for host in FORBIDDEN_ENDPOINT_HOSTS if host in text})
        if hits:
            offenders.append(f"{relative}: {', '.join(hits)}")
    assert not offenders, "LLM endpoint named outside the router:\n" + "\n".join(offenders)


def test_the_allowlist_itself_stays_minimal() -> None:
    """A regression guard: widening the allowlist must be a deliberate edit."""
    assert {"app/gateway/llm_router.py", "scripts/run_eval.py"} == ALLOWED_PATHS


def test_the_detector_recognises_a_violation() -> None:
    """Proves the scan would actually catch an SDK import, not just pass by luck."""
    tree = ast.parse("from google.genai import Client\nimport openai\n")
    modules = _imported_modules(tree)
    assert any(_violates(name) for name in modules)
    assert not _violates("app.gateway.tool_registry")


def test_backend_tree_is_scanned() -> None:
    """Fails if the scan silently covers nothing, e.g. after a layout change."""
    assert len(_python_files()) >= 1
