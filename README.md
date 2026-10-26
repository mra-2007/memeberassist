# MemberAssist

**A secure AI assistant for health-insurance members.**

MemberAssist answers coverage questions from the member's own plan documents **with citations**,
answers personal questions from the claims database **for the logged-in member only**, estimates
costs from real public price data, protects identity data behind an eight-layer security gateway,
and hands the conversation to a human with a summary when it is unsure.

Built for an Evernorth Health Services (The Cigna Group) case study in the CBIT AI Training
programme. It integrates three case studies into one product: conversational agents (primary),
prior-authorization automation, and claims & utilization analytics.

> **Every member, claim, provider link and amount in this deployment is synthetic.**
> No real patient data is used anywhere. The UI shows a "Synthetic demo data" badge at all times.
> This is a student demo that follows HIPAA-style safeguards; it is not a certified system.

---

## Status

Phase 0 (environment and scaffold) is complete. See **[PROGRESS.md](PROGRESS.md)** for what is
built, the evidence for it, the current metrics and anything blocked.
The full specification is **[MASTER_PROMPT.md](MASTER_PROMPT.md)**; the working rules are in
**[CLAUDE.md](CLAUDE.md)**.

---

## Quick start

### Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | latest | Manages Python 3.11 and the virtual environment |
| Python | 3.11 | `uv` will fetch it if missing |
| [Node.js](https://nodejs.org/) | ≥ 20 LTS | For the React app |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | latest | PostgreSQL 16 for development |

Minimum machine: 8 GB RAM and 15 GB free disk. 16 GB RAM is needed for the optional local
privacy mode (Ollama).

### Install uv

```bash
# Windows (PowerShell) — installs to %USERPROFILE%\.local\bin, no administrator rights needed
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Set up the project

```bash
git clone <this-repo> memberassist
cd memberassist

# 1. Python environment (one shared .venv for the root task runner and the backend)
uv sync

# 2. Secrets
cp .env.example .env          # Windows: copy .env.example .env
#    then fill in GEMINI_API_KEY and GROQ_API_KEY, and generate a JWT secret:
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Frontend packages
cd frontend && npm install && cd ..

# 4. Database (development)
uv run python manage.py db-up
uv run python manage.py db-wait

# 5. Git hooks (ruff + secret scan + architecture invariant)
uv run pre-commit install
```

### Run it

```bash
uv run python manage.py api     # FastAPI  -> http://127.0.0.1:8000  (docs at /api/docs)
uv run python manage.py web     # Vite     -> http://localhost:5173
```

The Vite dev server proxies `/api` to the backend, so the browser stays on one origin and the
authentication cookie remains first-party.

### Platform notes

- **Windows:** use PowerShell. `manage.py` resolves `npm`/`npx` shims itself, so the commands
  above work unchanged. Install CPU-only torch (the default here — there is no NVIDIA GPU
  requirement).
- **macOS:** Apple Silicon uses the MPS backend for the local models automatically.
- **Linux:** no special steps.

---

## Commands

Everything runs through `manage.py`, a Typer task runner (not `make`, so Windows works the same).

```bash
uv run python manage.py --help
```

| Command | Does |
| --- | --- |
| `env-report` | Machine and toolchain report |
| `api` / `web` | Start the backend / the frontend |
| `test` | Fast test suite (live and slow markers excluded) |
| `test --live` | Also run tests that hit real APIs and models |
| `lint` / `lint --fix` | ruff on the backend, ESLint on the frontend |
| `typecheck` | mypy on `app/gateway` and `app/tools` |
| `db-up` / `db-wait` | Start and wait for PostgreSQL |

Further commands (`check-models`, `download-models`, `seed`, `build-index`, `ask-index`, `eval`,
`demo-check`) appear as the phases that make them real are completed.

---

## Demo accounts

Seeded in Phase 1. All accounts and all their data are synthetic.

| Username | Role | Purpose |
| --- | --- | --- |
| `M10234` | member | Hero member for the demo: knee osteoarthritis, part of the deductible used |
| `M10555` | member | The "other member" used by the cross-member access attack tests |
| `agent` | agent | Receives human handoffs; sees the 3-line summary and the transcript |
| `admin` | admin | AI stack status page (and the insights dashboard from Phase 17) |
| `provider` | provider | Provider portal (Phase 11) |

Passwords come from `DEMO_PASSWORD` in `.env`, or are generated at seed time and printed once.

---

## Architecture

```
React app (member · agent · provider · admin)
     │  HTTPS · JWT in httpOnly cookie · streamed responses (SSE over fetch)
FastAPI ── auth ── rate limit ── size limits ── CORS / security headers
     │
SecureGateway — every message, every time
  [1] identity & access → [2] input guard → [3] mask PII/PHI (vault)
  → local signals: intent · injection · emotion → safety routes (no LLM)
  → memory + follow-up rewrite → router-first tool pre-fetch
  → [4] locked LLM call via llm_router (Gemini → Groq → OpenRouter | Ollama privacy mode)
  → [5] safe tool execution (allowlist · authorization · minimal fields · re-mask)
  → [6] output guard + citation check → [7] unmask → stream to UI
  → [8] audit log (hash-chained) · pipeline trace for the Security view
     │
Tools: RAG (Chroma + BM25 + reranker) · claims & benefits (PostgreSQL)
       · cost estimator (Python + CMS prices) · code explainer (Clinical Tables, MedlinePlus)
       · human handoff
```

**Rules go to RAG, personal facts go to tools.** "Is a knee MRI covered?" is answered from plan
documents with a citation. "What is my claim status?" is answered by a tool that queries
PostgreSQL behind the gateway. Member data never enters the vector index.

Diagrams live in `docs/architecture.md` (Phase 8).

---

## AI stack

Every model is real, integrated and visible on the admin **AI stack status** page
(`/api/health/models`). Model IDs are configuration, never hard-coded — `manage.py check-models`
lists what each provider actually offers our keys and writes the IDs to `.env`.

| Model / service | Runs | Job |
| --- | --- | --- |
| Gemini Flash (newest stable) | Cloud | Main reasoning: tool calling, grounded cited answers, explanations |
| Gemini Flash-Lite | Cloud | Backup for auxiliary tasks |
| Groq, large Llama-class | Cloud | First fallback for main reasoning; RAGAS judge |
| Groq, small fast model | Cloud | Follow-up rewriting, handoff summaries, borderline-injection judge |
| OpenRouter free model | Cloud | Second fallback |
| Ollama tool-calling model | Laptop | Optional privacy mode — every LLM task stays local |
| BAAI/bge-small-en-v1.5 | Local | RAG embeddings; features for the intent classifier |
| BAAI/bge-reranker-base | Local | Cross-encoder reranking |
| spaCy `en_core_web_lg` in Microsoft Presidio | Local | PII/PHI detection |
| protectai/deberta-v3-base-prompt-injection-v2 | Local | Prompt-injection classifier |
| Logistic regression on bge-small embeddings (trained by us) | Local | Intent classification and safety pre-routing |
| j-hartmann/emotion-english-distilroberta-base | Local | Frustration signal for handoff and sentiment |
| faster-whisper | Local | Voice input (Phase 14) |
| RapidOCR | Local | Text from uploaded claim images (Phase 9) |
| RAGAS (Gemini or Groq as judge) | Eval only | Faithfulness, answer relevancy, context precision |

Details, sizes, licences and measured latencies: `docs/ai_stack.md` (Phase 8).

---

## Security summary

Eight layers, applied to **every** message:

1. **Identity and access** — JWT (HS256) in an httpOnly, SameSite=Strict cookie with CSRF
   protection; the user's role and member ID come from the database, never from chat text.
2. **Input guard** — normalisation, length limits, rule patterns, a prompt-injection classifier,
   other-member identifier detection, and an auxiliary-LLM judge for borderline cases.
3. **PII/PHI masking** — Presidio + spaCy + custom recognizers + a per-session deny-list built
   from the user's own record. Placeholders stay consistent for a whole conversation; the vault
   lives only in server memory.
4. **Locked LLM call** — restrictive system prompt with a random canary, low temperature, fixed
   per-role tool allowlist, output and timeout limits.
5. **Safe tool execution** — Pydantic-validated arguments, ownership authorization, parameterised
   SQL only, minimal fields returned and re-masked.
6. **Output guard** — blocks raw identifiers, foreign placeholders, the canary, medical advice,
   off-allowlist links or images, and invalid citations.
7. **Unmask** — real values are restored only after the output guard passes.
8. **Audit log** — append-only and hash-chained, so tampering is detectable.

**No LLM — cloud or local — ever receives raw identity data.** A test asserts that captured LLM
payloads contain zero raw identifiers, and `backend/tests/test_architecture.py` enforces that only
the LLM router may import an LLM SDK.

Threat model, data-use note and the HIPAA-style safeguards statement: `docs/security.md`
(Phase 8).

---

## Limitations

- All member, claim, provider-link and accumulator data is **synthetic**. Plans, prices, codes and
  policy documents come from real public sources; the people do not exist.
- MemberAssist gives **no medical advice** — no diagnoses, doses or treatment recommendations.
  Emergency and self-harm messages are answered immediately from configured text, with no LLM call.
- Cost figures are **estimates** built on Medicare benchmark prices, not a payer's contracted
  rates, and are always labelled as such.
- Prior-authorization **decisions are made by humans**. MemberAssist only checks readiness and
  lists missing documentation.
- English only. Multilingual replies are out of scope.
- No payments, and no connection to any live insurer system.

---

## Data credits

Every source is recorded with its URL, licence and retrieval date in `docs/data_sources.md`.

- **CMS Marketplace API** and the CMS Exchange Public Use Files — plans, benefits and cost sharing.
- **CMS "Medicare Physician & Other Practitioners — by Geography and Service"** — price benchmarks.
- **NLM Clinical Tables** (ICD-10-CM, HCPCS) and **MedlinePlus Connect** — plain-language code
  explanations.
- **NPI Registry (CMS)** — real provider names and NPIs.
- **CMS/DOL Uniform Glossary** and **HealthCare.gov** — glossary and appeals guidance.
- **Medicare NCD 220.2** and the **Medicare Benefit Policy Manual** — coverage and
  prior-authorization criteria (public domain).
- **Synthea** (MITRE) — the synthetic patient population, re-homed to the chosen county.
- **openFDA**, **RxNav** (NLM) — drug labels and name normalisation (Phase 15).

MemberAssist uses its own brand and colours. No Evernorth or Cigna logos or branding are used.
