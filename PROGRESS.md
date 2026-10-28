# PROGRESS — MemberAssist

Live state of the build. Every phase is done only when its done-check passes **with pasted
evidence**. Specification: `MASTER_PROMPT.md`. Working rules: `CLAUDE.md`.

- **Current phase:** Phase 3 — Gateway skeleton, LLM router, core tools
- **Last updated:** 2026-10-28
- **Tags:** `v1-core` not yet reached (Phase 8) · `v2-final` not yet reached (Phase 18)

---

## Resolved blockers

| # | Item | Resolved | Evidence |
| --- | --- | --- | --- |
| B1 | PostgreSQL 16 in Docker | 2026-10-28 | Docker Desktop 29.9.2 / Compose v5.6.1 installed. `docker-compose.yml` **executed for the first time**; container healthy; `DATABASE_URL` switched from the SQLite fallback to PostgreSQL; migrations, the full seed and all 89 tests re-run against it. See "Phase 1 — re-verified on PostgreSQL" below. |

## Blocked

| # | Item | Why | Honest alternative in use | Needed to unblock |
| --- | --- | --- | --- | --- |
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
| Fast test suite | **89 passed** — 2.4 s on SQLite, 132 s on PostgreSQL |
| Full pipeline rebuild from an empty database | **68 s** (SQLite) · **~13 min** on PostgreSQL with expired HTTP caches |

Targets for the phases ahead:

| Metric | Target | Latest | Measured |
| --- | --- | --- | --- |
| Retrieval hit rate (right chunk in top 5) | ≥ 90% | **86%** (18/21) ❌ | 2026-10-28 |
| Answer correctness (judge + 10 manual) | ≥ 85% | — | needs the LLM (Phase 3) |
| Faithfulness (RAGAS) | ≥ 0.85 | — | needs the LLM (Phase 3) |
| Correct "I don't know" | ≥ 90% | **50%** (2/4) ❌ | 2026-10-28 |
| PII masking recall | ≥ 95% | — | needs the masker (Phase 3) |
| Attack blocking | 100% (benign false positives ≤ 5%) | — | needs the guard (Phase 4) |
| Latency p90 | < 5 s | — | needs the pipeline (Phase 3) |
| Intent classifier macro-F1 | reported | — | needs the classifier (Phase 4) |

**Both Phase 2 metrics are below target, and both have the same single cause.** The member's plan
document prices the benefit as "Rehabilitation services" and never says "physiotherapy"; the
retrieval misses are all that one vocabulary gap, and the confidence check fails because a
cross-encoder scores stylistic similarity rather than answerability. The fix is already-verified
Phase 1 data — `procedure_synonyms.csv` and `code_to_benefit.csv` — used to expand a query before
searching. The specification puts that with the answering step in Phase 3, so it is scheduled
there rather than half-built now. Full analysis in `docs/evaluation.md`.

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
- [x] `docker-compose.yml` with PostgreSQL 16 (executed and verified on 2026-10-28)
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
| Docker | **29.9.2** (Compose v5.6.1) — installed 2026-10-28, blocker B1 cleared ✅ |
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
$ uv run python manage.py db-stats     # the original run, on the SQLite fallback
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

### Phase 1 — re-verified on PostgreSQL (2026-10-28)

Docker Desktop became available, so blocker B1 is cleared. `docker-compose.yml` ran for the first
time and the whole of Phase 1 was replayed against PostgreSQL 16 rather than the SQLite fallback.
Nothing in the schema or the scripts needed changing: only portable column types were used, which
is what made the switch a configuration change.

```
$ docker compose ps
NAME              IMAGE                SERVICE   STATUS                   PORTS
memberassist-db   postgres:16-alpine   db        Up (healthy)             127.0.0.1:5432->5432/tcp

$ docker inspect --format='{{.State.Health.Status}}' memberassist-db
healthy

$ docker exec memberassist-db psql -U memberassist -d memberassist -tAc "select version();"
PostgreSQL 16.15 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit
```

`DATABASE_URL` in `.env` now points at PostgreSQL; the commented-out SQLite line is gone, so there
is no silent fallback left to trip over. Migrations applied onto an empty database:

```
$ uv run python manage.py migrate
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 9f2c54d436c1, initial schema
```

Full seed from empty, then the row counts and the test suite:

```
$ uv run python manage.py seed        # exit 0
$ uv run python manage.py db-stats
Database: PostgreSQL

accumulators 200    claims 718     data_sources 6    members 200
pa_requests  14     plan_benefits 536   plans 4      procedure_prices 43
provider_member_links 8   providers 64   refills 336   users 5
audit_log 0   conversations 0   feedback 0   handoffs 0   messages 0   uploads 0

Claims by status: 623 paid, 51 pending, 44 denied
Denial reasons:   OUT_OF_NETWORK 22, DUPLICATE 9, PA_MISSING 8, NOT_COVERED 5

$ uv run python manage.py test
89 passed in 132.02s

$ GET /api/health/config  ->  "database_backend": "postgresql"
```

The demo scenario survived the move intact: `manage.py demo-facts` still prints hero **M10234**
Tomás Correa on the Bronze HSA plan, the knee-MRI claim **CLM-4521** denied `PA_MISSING` for code
73721 at $583.76, and **PA-889** pending with its two missing documents.

**One honest finding: the pipeline is deterministic for a given date, not across dates.** The
claim count moved from 715 to 718 and the individual claim IDs and billed amounts changed, because
`make_synthetic_data` lays claims over a window that ends at *today* — the seed run was a day later
(2026-10-28 rather than 2026-10-27), so the window slid by a day and the trailing random draws
differ. `RANDOM_SEED` still makes a run reproducible on any machine **on the same date**, which is
what the demo needs, and the facts the demo script names are pinned explicitly rather than drawn at
random. It is worth knowing before anyone reads a changed row count as a defect.

The cost of PostgreSQL is test time: the fast suite went from **2.4 s to 132 s**, because the
data-integrity tests now make a TCP round trip per query instead of hitting a local file. Still
well inside "run it before and after every change", so it is being accepted rather than worked
around with a SQLite test database that would stop testing the real engine.

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

## Phase 2 — Knowledge base and RAG index ✅ done (2026-10-28)

- [x] `fetch_kb.py` + generated `data/kb/manifest.yaml` — **15 documents**, each with a SHA-256
- [x] Parsing: PyMuPDF text, pdfplumber tables → self-contained sentences, 138 benefit rows
- [x] Chunking by heading, 300–500 words prose / 160 words tables, rows never split
- [x] bge-small-en-v1.5 into persistent Chroma + BM25 on disk + index manifest with staleness checks
- [x] `manage.py ask-index` printing vector, BM25, fused and rerank scores
- [x] `eval/testset.yaml` — all 50 questions, with expected facts, sources, tools and behaviour
- [x] `manage.py eval-retrieval [--calibrate]` — hit rate and a reproducible threshold sweep
- [x] `tests/test_rag_parsing.py`, `tests/test_rag_retrieval.py`, `tests/test_kb_consistency.py`

**Done-check:** KB consistency and plan-filter tests pass; retrieval hit rate printed (86%).
**Two targets are not met.** They are reported, diagnosed and scheduled, not worked around.

### The knowledge base

```
$ uv run python manage.py fetch-kb
  ok   sbc_33602TX0461124   33602TX0461124  2,581,197B 10p
  ok   sbc_33602TX0870174   33602TX0870174  2,605,415B 10p
  ok   sbc_47501TX0030007   47501TX0030007    670,893B  7p
  ok   sbc_47501TX0040002   47501TX0040002    666,248B  7p
  ok   cms_uniform_glossary          ALL      143,061B  4p
  ok   hcgov_glossary_deductible     ALL      417,079B html
  ok   hcgov_glossary_copayment      ALL      418,868B html
  ok   hcgov_glossary_coinsurance    ALL      420,838B html
  ok   hcgov_glossary_oop_maximum    ALL      418,078B html
  ok   hcgov_appeals_internal        ALL      423,324B html
  ok   hcgov_appeals_external        ALL      430,931B html
  ok   medicare_ncd_220_2_mri        ALL      152,098B html
  ok   medicare_bpm_ch15_therapy     ALL    1,748,363B 311p -> pages 145-190
  ok   aetna_precertification_lists  ALL      155,767B html
  ok   memberassist_member_faq       ALL        8,882B authored
15 documents in data\kb\manifest.yaml
```

Four real issuer SBCs (one per plan, carrying that plan's `plan_id`) plus eleven shared documents
at `plan_id = ALL`. Every source, licence and retrieval date is in `docs/data_sources.md`.

The Benefit Policy Manual window is **located, not hard-coded**: `locate_therapy_pages()` reads
the chapter's own section headings and returns 220 ("Coverage of Outpatient Rehabilitation Therapy
Services") up to the page before 230, so a CMS revision is followed rather than mis-indexed.

### The index

```
$ uv run python manage.py build-index --force
145 prose chunks and 74 table chunks; words per chunk min 48 / median 151 / max 512
Document share warning — these shared documents each hold more than 25% of the index:
  medicare_bpm_ch15_therapy            73 chunks    33% of the index
Built in 69.3s

$ cat data/index/index_manifest.json
chunks 219 · embed BAAI/bge-small-en-v1.5 · rerank BAAI/bge-reranker-base
chunker v4 · docs 15 · built 2026-10-28
```

The manifest records the embedding model, the reranker, the chunker version and a hash per
document. `staleness_reasons()` compares it with the knowledge base and names every reason to
rebuild, so a stale index is reported rather than silently queried — verified in practice: bumping
the chunker printed *"chunker version changed: index built with v3, code is v4"* and rebuilt.

### Retrieval, end to end

```
$ uv run python manage.py ask-index "Is a knee MRI covered?" --member M10234
member M10234 -> plan 47501TX0040002

Is a knee MRI covered?  (plan filter: 47501TX0040002)
 # | Document              | Pg | Kind  | Vector        | BM25        | RRF     | Rerank | Found by
 1 | cms_uniform_glossary  |  3 | prose | 0.5791 (#12)  | -           | 0.01389 | 0.0040 | vector
 2 | sbc_47501TX0040002    |  2 | prose | 0.5923 (#6)   | -           | 0.02273 | 0.0033 | vector
 3 | cms_uniform_glossary  |  1 | prose | 0.5948 (#5)   | -           | 0.01538 | 0.0026 | vector
 4 | sbc_47501TX0040002    |  2 | table | 0.5868 (#8)   | -           | 0.02206 | 0.0016 | vector
 5 | sbc_47501TX0040002    |  6 | prose | 0.5827 (#10)  | 4.1151 (#20)| 0.04018 | 0.0014 | both
```

Vector top 20 + BM25 top 20, fused with reciprocal rank fusion (k = 60), reranked with
bge-reranker-base, best 5 kept. The plan filter is applied inside the vector query and again on
the BM25 results, and **comes from the session, never from the question**.

### Measurements

```
$ uv run python manage.py eval-retrieval --calibrate
Hit rate: 18/21 = 86%  (target >= 90%)
3 multi-turn item(s) scored on their first turn only; follow-up rewriting arrives in Phase 3.

Misses:
  T03 expected sbc_47501TX0040002:p3, got medicare_bpm_ch15_therapy p147/p174 ...
  T04 expected sbc_47501TX0040002:p4 or p3, got medicare_bpm_ch15_therapy p150/p160 ...
  T33 expected sbc_47501TX0040002:p3 or p2, got medicare_bpm_ch15_therapy p155/p145 ...

Confidence calibration — 21 answerable, 4 unanswerable
  answerable top scores  : min 0.01602 max 0.98213
  unanswerable top scores: min 0.00051 max 0.07514
Best threshold 0.01602 -> 23/25 correct
Correct "I don't know": 2/4 = 50%  (target >= 90%)
```

**All three retrieval misses are one defect.** The plan prices the benefit as "Rehabilitation
services" and never writes "physiotherapy" or "physical therapy". Inspecting the candidate pool
directly showed the SBC page 3 row **was never a candidate** — not mis-ranked, absent. No
reranking or weighting can recover a chunk that was never retrieved.

**The confidence threshold cannot be fixed by moving it.** The lowest-scoring *answerable*
question scores 0.01602 and the highest-scoring *unanswerable* one ("Will my insurance pay for
LASIK eye surgery?") scores 0.07514, higher than eight answerable questions. A cross-encoder
scores stylistic similarity, not answerability. The threshold in force is the fitted 0.016, and
`app/rag/confidence.py` records the number, the date, the command and what it actually achieves.

Both gaps have the same fix, and it is Phase 1 data we already verified:
`procedure_synonyms.csv` maps "knee mri" → 73721 and `code_to_benefit.csv` maps 73721 → the
benefit category a plan prices. The specification puts query expansion and the router-first tool
pre-fetch in Phase 3, so it is scheduled there.

### Tests

```
$ uv run python manage.py test      -> 151 passed, 10 deselected in 139s
$ uv run pytest                     -> 161 passed in 142s     (was 89 after Phase 1)
$ uv run python manage.py lint      -> All checks passed! · 33 files formatted · eslint clean
$ uv run python manage.py typecheck -> Success: no issues found in 28 source files
```

mypy now also covers `app/rag` and `app/ai`, because the retrieval path decides which plan's
documents a member may read and a type slip there is a confidentiality bug. Fixing it properly
(rather than with `type: ignore`) replaced two unchecked `int()` casts on Chroma metadata with a
narrowing helper, and turned a silent `zip(None)` into a named error that tells you to rebuild.

### What the measurements caught, and what changed because of it

Each of these was a real defect found by running the thing, not by reading the code.

| Found by | Defect | Fix |
| --- | --- | --- |
| Verifying the teammate FAQ's timelines against HealthCare.gov | The HTML parser enumerated `<p>`/`<li>`, but HealthCare.gov renders explainer paragraphs into bare `<div>`s. The **180-day appeal filing deadline** and the **45-day external-review decision** were in the fetched HTML and missing from the index — the single most-asked appeal deadline, silently absent. | Walk leaf blocks instead of a tag whitelist. The two appeals pages went from 628 and 451 words to 763 and 715. |
| First `ask-index` run on a knee-MRI question | All 40 candidates came from one 20,000-word Aetna bulletin on **breast** MRI; the member's own plan row never entered the pool. | Removed that document and filled the commercial-PA slot with Aetna's precertification lists (367 words, actually about prior authorization). Added a build-time warning when a shared document exceeds 25% of the index. |
| That new warning, on its first run | The Medicare manual was 53% of the index. | Narrowed to section 220 (coverage), dropping 230 (provider licensing, which no member asks about). Now 33%. |
| Inspecting the candidate pool for therapy questions | SBC page 3 groups **15 priced benefits into one chunk**, so the rehabilitation row was ~7% of its chunk. | Table chunks target 160 words, not 520. Rows still never split. |
| First build's chunk statistics | Six chunks were navigation — "Back to glossary", "Email this page". | Bidirectional runt merging plus a 12-word floor. Smallest chunk went from 3 words to 61. |
| Comparing authored and published documents | An explainer we wrote outranked the member's own SBC on 5 of 14 coverage questions. | Removed it. The authoritative source for what a plan pays is the plan's own document. |
| `tests/test_data_integrity.py` (a Phase 1 test) | The new `kb_documents` provenance row had prose where a URL belonged. | Points at the committed manifest. The Phase 1 test was right to insist. |

### Deviations from the specification, and why

| Deviation | Reason |
| --- | --- |
| The commercial advanced-imaging prior-authorization document is Aetna's **precertification lists**, not a clinical policy bulletin | CPB 0105 (breast MRI) was tried first and measured: it supplied every retrieval candidate for any imaging question, so the member's own plan never reached the reranker. eviCore's guideline PDFs 404 and its index is JavaScript-rendered. Recorded in `docs/data_sources.md`. |
| Only **section 220** of Benefit Policy Manual chapter 15, not 220 and 230 | 230 is provider licensing, not member coverage. Including it made the chapter 53% of the index. |
| Retrieval adds an **own-plan fusion weight (1.5×)** and a **per-document cap (2 chunks)** beyond the specified pipeline | Two shared documents contributed 187 of 303 chunks in the first build, against 15 for the member's own plan, and three coverage questions were answered out of the wrong document. Both are weights rather than filters, so a glossary question still reaches the glossary. |
| The reranker reads **title + section + text**, not text alone | A passage does not say which document it belongs to. With the title in view, a breast-MRI bulletin stops outranking the member's plan on a knee-MRI question. |
| **Table chunks target 160 words**, prose 420 | A benefit row only retrieves if it is a meaningful share of its own chunk. Measured, not guessed. |
| 15 documents, not the 16 first assembled | One authored explainer and one insurer bulletin were removed for the measured reasons above. Still inside the specification's 10–20. |
| The confidence check is a single threshold **and is reported as insufficient** | The specification asks for a calibrated threshold; it was built and calibrated. The honest result is 50% against a 90% target, so it is reported with its cause and its scheduled fix rather than presented as working. |

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

1. **Phase 3 — gateway skeleton, LLM router, core tools.** Blocked on `.env` keys (B2).
   - `manage.py check-models` / `download-models`; `app/gateway/llm_router.py` with the Gemini,
     Groq, OpenRouter and Ollama adapters and the documented fallback order.
   - Gateway layers 1, 3, 4, 5, 7, 8: identity, masking + vault, the locked LLM call, safe tool
     execution, unmasking, and the hash-chained audit log.
   - **Carry over from Phase 2, and do it before measuring retrieval again:** query expansion
     through `data/mappings/procedure_synonyms.csv` and `data/mappings/code_to_benefit.csv`, so
     "physiotherapy" reaches a document that says "Rehabilitation services". This is the fix for
     both of Phase 2's missed targets — the 86% hit rate and the 50% correct "I don't know".
     Re-run `manage.py eval-retrieval --calibrate` afterwards and re-fit the threshold.
   - `rag/answer.py`: `[S#]` citations, the citation check, and the "I couldn't find this in your
     plan documents" path with the offer of a human.
   - Core tools behind the allowlist; the cost estimator calling `app/services/cost_sharing.py`.
   - `/api/chat` with SSE.
2. **Save `.env`** with `GEMINI_API_KEY` and `GROQ_API_KEY` (blocker B2). Phase 3 cannot start
   without at least one.
3. **Cross-check the four plans against the live CMS Marketplace API** now that
   `CMS_MARKETPLACE_API_KEY` is set: one call first to confirm the response shape, then compare
   deductibles, out-of-pocket maximums and cost sharing against the PUF-derived rows, record the
   HIOS ids and any differences in `docs/data_sources.md`, and correct only what differs. The PUF
   path stays as the fallback. The key expires every 60 days.
