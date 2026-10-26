# PROGRESS — MemberAssist

Live state of the build. Every phase is done only when its done-check passes **with pasted
evidence**. Specification: `MASTER_PROMPT.md`. Working rules: `CLAUDE.md`.

- **Current phase:** Phase 1 — Data and database
- **Last updated:** 2026-10-27
- **Tags:** `v1-core` not yet reached (Phase 8) · `v2-final` not yet reached (Phase 18)

---

## Blocked

| # | Item | Why | Honest alternative in use | Needed to unblock |
| --- | --- | --- | --- | --- |
| B1 | PostgreSQL 16 in Docker | Docker is not installed on this machine (`docker` not on PATH; `manage.py env-report` confirms). Docker Desktop needs administrator rights to install. | None yet. Phase 1 will run on a **SQLite** development database and `DATABASE_URL` will be switched back to PostgreSQL as soon as Docker is available. `docker-compose.yml` is written and ready but **has not been executed**, so it is unverified. | Install Docker Desktop for Windows, then `uv run python manage.py db-up` |
| B2 | LLM provider keys | `GEMINI_API_KEY`, `GROQ_API_KEY` and `OPENROUTER_API_KEY` are empty in `.env`. No LLM call can be made or verified. | None — Phase 3 cannot start without at least one key. | Add keys to `.env` (see "Keys needed" below) |
| B3 | CMS Marketplace API key | `CMS_MARKETPLACE_API_KEY` is empty. Keys take time to issue and expire every 60 days. | Phase 1 falls back to the CMS Exchange Public Use Files (Plan Attributes PUF + Benefits and Cost Sharing PUF) if the key has not arrived. | Request at <https://developer.cms.gov/marketplace-api> |
| B4 | Ollama privacy mode (optional, AI stack #6) | Machine has **13.7 GB** total RAM with only ~1.7 GB free at measurement time. A 7–8 B tool-calling model needs roughly 6 GB resident on top of the five local models MemberAssist already loads. The master prompt requires 16 GB for Ollama. | Privacy mode is **deferred and will be reported as not available on this machine** rather than half-implemented. Cloud fallback order (Gemini → Groq → OpenRouter) covers reliability. | A machine with ≥ 16 GB RAM, or accept that privacy mode stays out of scope |
| B5 | Synthea generation from source | Java 17+ is not installed (`java` not on PATH). | Use Synthea's **pre-generated CSV sample** from <https://synthea.mitre.org/downloads> (explicitly allowed by the master prompt, section 7.6). | Install a JDK only if the pre-generated sample proves insufficient |
| B6 | MedGemma 4B comparison (optional, AI stack #15) | Needs a Colab GPU session; not available locally. | Phase 11 keeps it optional, as specified. | A Google Colab session when Phase 11 arrives |

---

## Keys needed from the team

Add these to `.env` (never commit it). `JWT_SECRET`, `POSTGRES_PASSWORD` and `DEMO_PASSWORD`
were generated automatically and are already set.

| Variable | Where to get it | Needed by |
| --- | --- | --- |
| `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | Phase 3 (main reasoning) |
| `GROQ_API_KEY` | <https://console.groq.com/keys> | Phase 3 (fallback + auxiliary tasks) |
| `OPENROUTER_API_KEY` (optional) | <https://openrouter.ai/keys> | Phase 3 (second fallback) |
| `CMS_MARKETPLACE_API_KEY` | <https://developer.cms.gov/marketplace-api> — **request now, it takes time** | Phase 1 (real plan data) |
| `OPENFDA_API_KEY` (optional) | <https://open.fda.gov/apis/authentication/> | Phase 15 (drug labels) |

---

## Metrics

No metrics yet — the first numbers arrive in Phase 2 (retrieval hit rate on the first 20
questions). Targets, for reference:

| Metric | Target | Latest | Measured |
| --- | --- | --- | --- |
| Retrieval hit rate (right chunk in top 5) | ≥ 90% | — | — |
| Answer correctness (judge + 10 manual) | ≥ 85% | — | — |
| Faithfulness (RAGAS) | ≥ 0.85 | — | — |
| Correct "I don't know" | ≥ 90% | — | — |
| PII masking recall | ≥ 95% | — | — |
| Attack blocking | 100% (benign false positives ≤ 5%) | — | — |
| Latency p90 | < 5 s | — | — |
| Intent classifier macro-F1 | reported | — | — |

---

## Phase 0 — Environment and scaffold ✅ done (2026-10-27)

- [x] Machine and toolchain report (below)
- [x] `git init`, `.gitignore`, pre-commit with ruff + gitleaks secret scan
- [x] Section 6 directory layout
- [x] Backend: uv workspace, Python 3.11, FastAPI + SQLAlchemy + Pydantic v2 installed
- [x] Frontend: Vite + React 19 + TypeScript (strict) + Tailwind CSS v4 + React Router + Recharts
      + lucide-react
- [x] `CLAUDE.md`, `PROGRESS.md`, `README.md`
- [x] `.env.example` with every key; `.env` created with a generated `JWT_SECRET`
- [x] `docker-compose.yml` with PostgreSQL 16 (written, **not executed** — see B1)
- [x] `manage.py` (Typer) with real commands
- [x] FastAPI `/api/health`, `/api/health/config`, `/api/health/models`
- [x] First tests: architecture invariant, health API, config parsing

### Environment report

| Item | Value |
| --- | --- |
| OS | Windows 11 Home Single Language 10.0.26300 (64-bit) |
| CPU | 13th Gen Intel Core i7-13700H — 14 cores / 20 threads |
| RAM | 13.7 GB total (1.7 GB free at measurement) — **below the 16 GB Ollama needs** |
| GPU | Intel Iris Xe Graphics (no NVIDIA) → **CPU-only torch** |
| Disk free | 675 GB on the repository drive (need ≥ 15 GB) ✅ |
| git | 2.51.0.windows.1 ✅ |
| uv | 0.13.5 — **installed during this phase** (user-level, no admin needed) ✅ |
| Python | 3.11.9 selected for the venv (3.13 and 3.14 also present) ✅ |
| Node | v24.14.1 ✅ (need ≥ 20) |
| npm | 11.11.0 ✅ |
| Docker | **not installed** → blocker B1 |
| Ollama | not installed → optional, blocker B4 |
| Java | not installed → blocker B5 |

### Evidence

`uv run python manage.py --help`:

```
 Usage: manage.py [OPTIONS] COMMAND [ARGS]...
 MemberAssist development and operations commands.
┌─ Commands ──────────────────────────────────────────────────────────────────┐
│ env-report  Print the machine and toolchain report (Phase 0 done-check).    │
│ lint        Run ruff on the backend (and ESLint on the frontend when it     │
│             exists).                                                        │
│ typecheck   Run mypy over the gateway and tools packages.                   │
│ test        Run the fast test suite (the live and slow markers are excluded │
│             by default).                                                    │
│ api         Start the FastAPI backend with uvicorn.                         │
│ web         Start the Vite frontend dev server.                             │
│ db-up       Start the PostgreSQL container used for development.            │
│ db-wait     Block until the development database accepts TCP connections.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

Live backend, started with uvicorn on 127.0.0.1:8000:

```
GET /api/health -> 200
{"status":"ok","app":"memberassist","version":"0.1.0","env":"development",
 "uptime_seconds":0.3,"synthetic_data":true}

Security headers on the response:
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'

GET /api/health/config -> 200
{"llm_providers_configured":{"gemini":false,"groq":false,"openrouter":false,"ollama":false},
 "cms_marketplace_key_present":false,"database_backend":"postgresql","privacy_mode":false,
 "plan_scope":{"state":"TX","county_fips":"48113","year":2026},
 "missing_required":["GEMINI_API_KEY or GROQ_API_KEY (at least one LLM provider)"]}
```

Frontend dev server (`npm run dev`, Vite 8.3.5) serving and proxying to the backend:

```
GET http://localhost:5173/          -> 200  (index.html with <div id="root">)
GET http://localhost:5173/src/main.tsx  -> 200, 2004 bytes
GET http://localhost:5173/src/index.css -> 200, 15980 bytes (Tailwind compiled)
GET http://localhost:5173/api/health    -> 200  (proxied to FastAPI)
{"status":"ok","app":"memberassist","version":"0.1.0","env":"development",
 "uptime_seconds":0.6,"synthetic_data":true}
```

Production build of the frontend:

```
> tsc -b && vite build
vite v8.3.5 building client environment for production...
✓ 1935 modules transformed.
dist/index.html                   0.62 kB │ gzip:  0.37 kB
dist/assets/index-CzpnW7e1.css   12.28 kB │ gzip:  3.25 kB
dist/assets/index-C3vmChcU.js   226.70 kB │ gzip: 71.58 kB
✓ built in 712ms
```

Fast test suite, lint and typecheck:

```
$ uv run pytest -m "not live and not slow"
.................                                                        [100%]
17 passed in 0.15s

$ uv run ruff check .      -> All checks passed!
$ uv run ruff format --check .  -> 18 files already formatted
$ npm run lint (eslint .)  -> exit 0, no findings
$ uv run mypy              -> Success: no issues found in 2 source files
$ npx tsc -b --noEmit      -> exit 0
```

### Deviations from the specification, and why

| Deviation | Reason |
| --- | --- |
| Python dependencies live in a **uv workspace** (root `pyproject.toml` + `backend/`) with one shared `.venv` | `manage.py` sits at the repository root and needs Typer; a workspace gives both the root runner and the backend one environment and one lock file. The backend remains an installable package. |
| Heavier model stacks are **uv extras** (`rag`, `security`, `ai`, `llm`, `voice`, `ocr`, `docgen`) rather than base dependencies | Keeps Phase 0/1 setup to seconds instead of downloading torch before it is needed. Each phase installs its own extra. |
| `Caddyfile` not created yet | It belongs to Phase 8 (deployment); writing an unused placeholder would violate the "no stubs" rule. |
| `/api/health/models` returns `readiness: "not_implemented_until_phase_3"` | Honest placeholder state. It does **not** invent model statuses before the AI registry exists. |

---

## Phase 1 — Data and database ⬜ not started

- [ ] SQLAlchemy models for every table in section 8 + Alembic migration
- [ ] `fetch_plans.py` (CMS Marketplace API, PUF fallback) — 3–4 real plans with cost sharing
- [ ] `fetch_prices.py` — CMS "Medicare Physician & Other Practitioners by Geography and Service"
- [ ] Code → benefit mapping and plain-name synonyms, every ICD-10 verified via Clinical Tables
- [ ] Synthea sample download and conversion; ~200 members re-homed to the chosen county
- [ ] Providers from the NPI Registry (6–8 specialties)
- [ ] Claims with documented status rules; accumulators computed from paid claims
- [ ] Demo scenario (M10234, CLM-4521) with real plan numbers; demo users seeded
- [ ] `docs/data_sources.md`
- [ ] `tests/test_accumulators.py`, status-rule tests

**Done-check:** row counts printed; the hero member's plan, accumulators and claims printed;
accumulator and status-rule tests pass; every source recorded with URL and retrieval date.

## Phase 2 — Knowledge base and RAG index ⬜ not started

- [ ] `fetch_kb.py` + `data/kb/manifest.yaml` (10–20 documents)
- [ ] Parsing (PyMuPDF text, pdfplumber tables → self-contained sentences), chunking, metadata
- [ ] bge-small embeddings into Chroma + BM25 index + index manifest
- [ ] `manage.py ask-index` printing vector, BM25 and rerank scores
- [ ] First 20 questions of `eval/testset.yaml`

**Done-check:** KB consistency test and plan-filter test pass; retrieval hit rate printed.

## Phase 3 — Gateway skeleton, LLM router, core tools ⬜ not started

- [ ] Layers 1, 3, 4, 5, 7, 8 (identity, masking + vault, locked LLM call, safe tools, unmask, audit)
- [ ] `check-models`, `download-models`, `llm_router` with Gemini/Groq/OpenRouter/Ollama adapters
- [ ] Core tools; RAG answering with citations; cost estimator; code explainer
- [ ] `/api/chat` with SSE

**Done-check:** real transcripts for a cited coverage answer, a denied-claim explanation, a cost
estimate, an out-of-documents refusal and a forced Gemini → Groq fallback; payload-capture test
shows zero raw identifiers; per-stage latency logged.

## Phase 4 — Guards and local AI models ⬜ not started

- [ ] Input guard (layer 2), output guard (layer 6)
- [ ] Intent classifier trained and evaluated; router-first path enabled
- [ ] Rate limiting, CORS, security headers, logging filter, architecture test
- [ ] `eval/attacks.yaml` v1 and `eval/benign.yaml`

**Done-check:** 100% of attacks blocked, benign false positives ≤ 5%; classifier metrics
reported; audit chain verified.

## Phase 5 — Conversation design ⬜ not started

- [ ] Memory + follow-up rewrite, safety routes, feedback, "Tell me more", stage events
- [ ] Emotion model frustration signal; all handoff triggers; summaries; containment

**Done-check:** scripted conversations show memory, an emergency route, a medical-advice refusal,
and each handoff trigger firing with its summary.

## Phase 6 — Frontend ⬜ not started

- [ ] Login, member chat, Security view, agent console, AI stack status

**Done-check:** demo flow runs in the browser; screenshots in `docs/screenshots/`.

## Phase 7 — Evaluation and hardening ⬜ not started

- [ ] 50-question test set, 30+ attacks, 100 PII cases
- [ ] RAGAS run; thresholds calibrated; latency optimised

**Done-check:** every target met; `docs/evaluation.md` written.

## Phase 8 — Deployment, docs, `v1-core` ⬜ not started

- [ ] Docker Compose (db + backend + frontend behind Caddy with HTTPS), health checks
- [ ] README and all section 17 documents; `demo_check.py` passes
- [ ] Tag `v1-core`

## Phases 9–17 — Waiting list ⬜ not started

| Phase | Feature |
| --- | --- |
| 9 | Explain-my-claim upload |
| 10 | Appeal letter drafting |
| 11 | PA status + provider mode + PA pre-fill |
| 12 | Find a doctor |
| 13 | Proactive suggestions |
| 14 | Voice input |
| 15 | Medication reminders + refill alerts + drug coverage |
| 16 | Sentiment tracking |
| 17 | Insights dashboard |

## Phase 18 — Final, `v2-final` ⬜ not started

- [ ] Full regression and redeploy; `docs/report_material.md`; demo rehearsal checklist
- [ ] Tag `v2-final`

---

## Next steps (exact)

1. Add `GEMINI_API_KEY` and `GROQ_API_KEY` to `.env`; request the CMS Marketplace API key.
2. Install Docker Desktop, then `uv run python manage.py db-up` to clear blocker B1.
3. Begin Phase 1: SQLAlchemy models for section 8, Alembic set-up, then `fetch_plans.py`.
