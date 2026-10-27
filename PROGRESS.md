# PROGRESS — MemberAssist

Live state of the build. Every phase is done only when its done-check passes **with pasted
evidence**. Specification: `MASTER_PROMPT.md`. Working rules: `CLAUDE.md`.

- **Current phase:** Phase 2 — Knowledge base and RAG index
- **Last updated:** 2026-10-27
- **Tags:** `v1-core` not yet reached (Phase 8) · `v2-final` not yet reached (Phase 18)

---

## Blocked

| # | Item | Why | Honest alternative in use | Needed to unblock |
| --- | --- | --- | --- | --- |
| B1 | PostgreSQL 16 in Docker | Docker is not installed on this machine (`docker` not on PATH; `manage.py env-report` confirms). Docker Desktop needs administrator rights to install. | None yet. Phase 1 will run on a **SQLite** development database and `DATABASE_URL` will be switched back to PostgreSQL as soon as Docker is available. `docker-compose.yml` is written and ready but **has not been executed**, so it is unverified. | Install Docker Desktop for Windows, then `uv run python manage.py db-up` |
| B2 | LLM provider keys | Still empty in `.env` as of the Phase 1 commit. The team reported adding Gemini and Groq keys, but the file on disk was unchanged (all four key values zero-length), so the edit was probably unsaved. No LLM call can be made or verified. | None — Phase 3 cannot start without at least one key. Phases 1 and 2 need none. | Save `.env` with `GEMINI_API_KEY` and `GROQ_API_KEY` |
| B3 | CMS Marketplace API key | `CMS_MARKETPLACE_API_KEY` is empty; requested, not yet issued. Calling the API without it returns `401 {"message":"No API key found in request"}`. | **Resolved for Phase 1** by the CMS Exchange Public Use Files, which need no key and carry the same filings. Still needed for Phase 15 drug coverage. | Wait for <https://developer.cms.gov/marketplace-api> |
| B4 | Ollama privacy mode (optional, AI stack #6) | Machine has **13.7 GB** total RAM with only ~1.7 GB free at measurement time. A 7–8 B tool-calling model needs roughly 6 GB resident on top of the five local models MemberAssist already loads. The master prompt requires 16 GB for Ollama. | Privacy mode is **deferred and will be reported as not available on this machine** rather than half-implemented. Cloud fallback order (Gemini → Groq → OpenRouter) covers reliability. | A machine with ≥ 16 GB RAM, or accept that privacy mode stays out of scope |
| B5 | Synthea generation from source | Java 17+ is not installed (`java` not on PATH). | Use Synthea's **pre-generated CSV sample** from <https://synthea.mitre.org/downloads> (explicitly allowed by the master prompt, section 7.6). | Install a JDK only if the pre-generated sample proves insufficient |
| B7 | CMS "Medicare Physician & Other Practitioners — by Geography and Service" dataset | `data.cms.gov` returns **HTTP 403 (Akamai "Access Denied")** from this network on every needed path — `/data.json`, `/data-api/*`, `/api/1/metastore/*` and the dataset pages — with any User-Agent, from both httpx and curl. Only `/provider-data/*` (a different catalogue) responds. `catalog.data.gov` CKAN returns 404; `healthdata.gov` does not carry the dataset. It is the only public source for **average submitted charges**. | Prices come from the **CMS Physician Fee Schedule** relative value files and the **Clinical Laboratory Fee Schedule**, both on `www.cms.gov`, which respond normally. All 34 codes are priced from real CMS data. `avg_submitted_charge` is left NULL, so the cost estimator will give a single Medicare-benchmark estimate rather than a low–high range, and will say so. **No multiplier was invented to manufacture a range.** `fetch-prices` re-probes the blocked host on every run. | Run `manage.py fetch-prices` from an unblocked network; the column fills and the range appears automatically |
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

Retrieval and answer metrics arrive in Phase 2–3. Phase 1 produced the dataset figures below.

| Phase 1 measure | Value |
| --- | --- |
| ICD-10-CM codes verified against NLM Clinical Tables | **66 / 66** (4 wrong codes caught and fixed) |
| Procedure codes with a real CMS price | **34 / 34** (29 from the Physician Fee Schedule, 5 from the Clinical Laboratory Fee Schedule) |
| Plain-language synonyms resolving to a real code | **80 / 80** |
| Fast test suite | **89 passed** in 2.4 s |
| Full pipeline rebuild from an empty database | **68 s** |

Targets for the phases ahead:

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

## Phase 1 — Data and database ✅ done (2026-10-27)

- [x] SQLAlchemy models for every table in section 8 + Alembic migration
- [x] `fetch_plans.py` — 4 real plans with 536 cost-sharing rows from the CMS Marketplace PUFs
- [x] `fetch_prices.py` — 43 price rows from the CMS Physician Fee Schedule and Clinical
      Laboratory Fee Schedule (the specified `data.cms.gov` dataset is blocked — blocker B7)
- [x] `verify_mappings.py` — 66 ICD-10 codes verified against NLM Clinical Tables, 34 procedure
      codes checked against CMS descriptors, 80 synonyms resolved
- [x] Synthea sample download and conversion; 200 adults re-homed to Dallas County
- [x] `fetch_providers.py` — 64 real NPI providers across 8 specialties
- [x] 715 claims with documented status rules; accumulators computed from paid claims
- [x] Demo scenario (M10234, CLM-4521, PA-889) using real plan numbers; 5 demo users seeded
- [x] `docs/data_sources.md`
- [x] `app/services/cost_sharing.py` — the section 12 formula, shared by claims and (from
      Phase 3) the cost estimator
- [x] `tests/test_cost_sharing.py` (44 tests) and `tests/test_data_integrity.py` (28 tests)

**Done-check:** row counts printed; the hero member's plan, accumulators and claims printed;
accumulator and status-rule tests pass; every source recorded with URL, licence and date.

### Row counts

```
$ uv run python manage.py db-stats
Database: SQLite (temporary fallback — blocker B1)

accumulators 200    claims 715     data_sources 6    members 200
pa_requests  14     plan_benefits 536   plans 4      procedure_prices 43
provider_member_links 8   providers 64   refills 336   users 5
audit_log 0   conversations 0   feedback 0   handoffs 0   messages 0   uploads 0
```

Claims by status: **613 paid, 53 pending, 48 denied** — and all four denial reasons occur, which
the tests require: `OUT_OF_NETWORK` 25, `DUPLICATE` 11, `PA_MISSING` 7, `NOT_COVERED` 5.

### Recorded data sources

| Key | Source | Retrieved | Rows |
| --- | --- | --- | --- |
| `cms_marketplace_puf` | CMS Marketplace Public Use Files, plan year 2026 | 2026-10-27 | 536 |
| `cms_pfs_rvu` | CMS Physician Fee Schedule relative value files, 2026 | 2026-10-27 | 38 |
| `cms_clfs` | CMS Clinical Laboratory Fee Schedule, 2026 | 2026-10-27 | 5 |
| `cms_npi_registry` | CMS NPI Registry, API version 2.1 | 2026-10-27 | 64 |
| `nlm_clinical_tables` | NLM Clinical Tables (ICD-10-CM and HCPCS) | 2026-10-27 | 66 |
| `synthea_sample` | Synthea 10,000-patient sample (MITRE, Apache 2.0) | 2026-10-27 | 200 |

### The four real plans

```
33602TX0461124  Gold             HMO  ded $  500.00  oop $ 8500.00  HSA=n  Blue Cross and Blue Shield of TX
33602TX0870174  Silver           POS  ded $ 1250.00  oop $10500.00  HSA=n  Blue Cross and Blue Shield of TX
47501TX0030007  Gold             POS  ded $  700.00  oop $ 7500.00  HSA=n  WellPoint
47501TX0040002  Expanded Bronze  HMO  ded $ 4000.00  oop $ 8450.00  HSA=y  WellPoint
```

Chosen from the 138 individual medical plans sold in Dallas County, one per design, with the
reason for each recorded in `data/mappings/selected_plans.csv`. `fetch-plans` refuses to seed a
plan set where no plan prices advanced imaging as coinsurance after the deductible, because the
demo and the cost tests depend on that design existing.

### Hero member (the demo's done-check)

```
$ uv run python manage.py demo-facts

HERO M10234: Tomás Correa, dob 1961-01-28
  address  114 Feil Stravenue, Dallas, TX 75252
  contact  (214) 555-0100 / tomas.correa0@example.invalid
  plan     47501TX0040002 WellPoint
           Wellpoint Essential Bronze 4000 HSA (+ Incentives)
           Expanded Bronze HMO, HSA-eligible: True
  deductible $4000.00 (used $929.29, $3070.71 left)
  oop max    $8450.00 (used $929.29, $7520.71 left)
  imaging in_network      '40.00% Coinsurance after deductible' (covered: True)
  imaging out_of_network  '100.00%' (covered: True)

HERO CLAIMS
  CLM-1006   2026-10-12 99203 office   in  billed $260.35 allowed $117.57 plan $0.00 you $  0.00 PENDING          dx=['E11.22','E88.810']
  CLM-4521   2026-10-03 73721 office   in  billed $583.76 allowed $204.41 plan $0.00 you $583.76 DENIED/PA_MISSING dx=['M17.11']
  CLM-6608   2026-09-21 80053 office   in  billed $ 32.11 allowed $ 10.56 plan $0.00 you $ 10.56 PAID             dx=['E11.22','E88.810']
  CLM-3730   2026-09-14 99203 office   in  billed $346.02 allowed $117.57 plan $0.00 you $117.57 PAID             dx=['E11.22','E88.810']
  CLM-1551   2026-08-24 80053 office   in  billed $ 21.95 allowed $ 10.56 plan $0.00 you $ 10.56 PAID             dx=['E11.22','E88.810']
  CLM-4470   2026-08-18 99285 facility in  billed $503.16 allowed $171.35 plan $0.00 you $171.35 PAID             dx=['U07.1','E11.22']
  CLM-6663   2026-08-18 99213 office   in  billed $283.71 allowed $ 95.19 plan $0.00 you $ 95.19 PAID             dx=['E11.22','E88.810']
  CLM-2110   2026-08-17 99203 office   in  billed $280.11 allowed $117.57 plan $0.00 you $117.57 PAID             dx=['E11.22','E88.810']
  CLM-1750   2026-08-10 99285 facility in  billed $310.14 allowed $171.35 plan $0.00 you $171.35 PAID             dx=['E11.22','E88.810']
  CLM-3099   2026-07-20 99203 office   in  billed $237.64 allowed $117.57 plan $0.00 you $117.57 PAID             dx=['E11.22','E88.810']
  CLM-1426   2026-07-13 99203 office   in  billed $292.53 allowed $117.57 plan $0.00 you $117.57 PAID             dx=['E11.22','E88.810']

PA-889  PENDING, code 73721, submitted 2026-10-22
  missing: Notes from a recent visit about the knee
  missing: Record of at least six weeks of conservative treatment

Next refill  Acetaminophen 500 MG Oral Tablet (RxCUI 198440) due 2026-10-30
ATTACK-TEST MEMBER M10555: Jolene Krajcik, 10 claims
```

The plan pays $0.00 on these claims and that is **correct, not a bug**: it is a Bronze HSA plan
where almost everything is subject to the deductible, and the member has $3,070.71 of a $4,000
deductible left. 432 claims across the population do have a plan payment, on the Gold and Silver
designs. The hero was deliberately put on the Bronze plan because it makes the demo's cost
question substantive.

### Price spot check (real CMS figures)

```
73721 office    allowed $ 204.41  medicare pays $163.53  [PFS]   Mri jnt of lwr extre w/o dye
99213 office    allowed $  95.19  medicare pays $ 76.15  [PFS]   Office o/p est low 20 min
99213 facility  allowed $  57.45  medicare pays $ 45.96  [PFS]   Office o/p est low 20 min
85025 office    allowed $   7.77  medicare pays $  7.77  [CLFS]  Complete cbc w/auto diff wbc
27447 facility  allowed $1159.35  medicare pays $927.48  [PFS]   Total knee arthroplasty
```

Hand-checked: 73721's total non-facility RVU is 6.12 and the 2026 conversion factor is 33.4009;
6.12 × 33.4009 = **$204.41**, matching the stored row exactly.

### Tests

```
$ uv run python manage.py test
89 passed in 2.38s

$ uv run python manage.py lint        -> All checks passed! · 40 files already formatted · eslint clean
$ uv run python manage.py typecheck   -> Success: no issues found in 18 source files
```

The 89 include 44 cost-sharing tests (the specification's `$900 deductible + 20% coinsurance on
$1,200 = $960` example is asserted exactly) and 28 data-integrity tests, among them the
accumulator recomputation the specification asks for by name.

### Full pipeline, reproducible from empty

```
$ rm data/memberassist-dev.sqlite3 && uv run python manage.py seed
...
Done in 68 s
```

Deterministic from `RANDOM_SEED = 20261027`: the same database on every machine. Raw downloads
are cached under `data/raw/` (git-ignored), so re-runs do not re-fetch from CMS.

### What the tests caught, and what changed because of it

Worth recording, because each was a real defect rather than a test adjustment:

| Found by | Defect | Fix |
| --- | --- | --- |
| `test_benefit_types` | `0.00% Coinsurance after deductible` — 1,369 real rows in the Texas filings — fell through as "unpriced", so those plans could not be costed at all. | A zero rate is still a rate: the member pays their deductible, then nothing. |
| `test_pa_missing_denials_have_no_approved_prior_authorization` | A member could hold an approved knee-MRI authorization *and* a missing-authorization denial for a knee MRI. | The PA decision is made once per member and service, and every claim for it inherits that decision. |
| `test_accumulators_recompute_from_the_paid_claims` | Out-of-network coinsurance was applied to the full billed charge, overstating what the plan pays and crediting balance-billed amounts to the out-of-pocket maximum. | `settle_out_of_network()`: cost sharing applies to the plan's allowed amount, the provider bills the balance on top, and that balance counts toward neither limit. `claims.applied_to_oop` records what did count. |
| NPI probe | The registry's `city` filter matches mailing addresses, and `taxonomy_description` matches any taxonomy on a record — a Dallas radiology search returned a Minneapolis neurologist. | Results are re-checked against the `LOCATION` address and the taxonomy that actually matched. |
| `verify-mappings` | Four ICD-10 codes were wrong: `E88.81`, `T14.8`, `Z59.1` invalid; `U07.1` wrongly rejected by our own shape filter. | Corrected to `E88.810`, `T14.8XXA`, `Z59.10`; the filter now accepts U-codes. |

### Deviations from the specification, and why

| Deviation | Reason |
| --- | --- |
| Plans come from the **CMS Marketplace PUFs**, not the Marketplace API | The API returns 401 without a key (blocker B3). The specification allows the PUFs; they carry the same filings. |
| Prices come from the **Physician Fee Schedule** and **Clinical Laboratory Fee Schedule**, not the utilization dataset | `data.cms.gov` is blocked (blocker B7). `avg_submitted_charge` is NULL, so the estimator gives a single benchmark figure rather than a range — stated plainly rather than faked with a multiplier. |
| The Gold plan is a **POS**, not a PPO | No PPO is sold on the Dallas County exchange for 2026. POS is the closest design with out-of-network coverage. |
| Member IDs are `M10001`–`M10198` **plus** `M10234` and `M10555` | Those two IDs are named by the demo script, and `M10555` falls outside any 200-long run from `M10001`. |
| The **10,000-patient** Synthea sample, not the "latest" one | "latest" has 111 patients and only 77 living adults — too few for the ~200 the project wants. |
| `app/services/cost_sharing.py` created in Phase 1, though section 12 belongs to Phase 3 | Claim settlement and cost estimation are the same arithmetic. One implementation means a claim and an estimate for the same service can never disagree. |
| Two procedure codes added beyond the specification's list: `97810` acupuncture, `43644` gastric bypass | Both are priced by CMS and excluded by all four selected plans, which is what makes `NOT_COVERED` denials and "is this covered?" answers real rather than contrived. |
| The schema has one squashed initial migration | Pre-release, with no deployed database and a reproducible pipeline. One migration matching the models is more honest than a chain that cannot replay onto a populated table. |
| `claims.applied_to_oop` added beyond section 8 | Without it, the accumulator cannot be independently recomputed from the claims once balance billing exists. |

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

1. **Phase 2 — knowledge base and RAG index.** No keys needed; the SBC URLs for all four plans
   are already captured in `data/mappings/selected_plans.csv`.
   - `scripts/fetch_kb.py` + `data/kb/manifest.yaml`: the four plans' real SBC PDFs, the CMS/DOL
     Uniform Glossary, HealthCare.gov appeals pages, Medicare NCD 220.2, the outpatient-therapy
     sections of the Medicare Benefit Policy Manual, one public commercial imaging
     prior-authorization policy, and our own FAQs.
   - Install the RAG and AI extras: `uv sync --extra rag --extra ai`.
   - Parse with PyMuPDF and pdfplumber, rewrite table rows as self-contained sentences, chunk by
     heading, embed with bge-small into Chroma, build the BM25 index, write the index manifest.
   - `manage.py ask-index` printing vector, BM25 and rerank scores.
   - First 20 questions of `eval/testset.yaml`; print the retrieval hit rate.
   - `tests/test_kb_consistency.py`: every `plan_benefits` number must agree with that plan's SBC
     text, and a plan filter test proving another plan's chunk is never returned.
2. **Save `.env`** with `GEMINI_API_KEY` and `GROQ_API_KEY` (blocker B2) — needed before Phase 3,
   not before Phase 2.
3. **Install Docker Desktop**, then `manage.py db-up && manage.py migrate && manage.py seed` and
   restore the PostgreSQL `DATABASE_URL` (blocker B1). The commented-out URL is in `.env`.
