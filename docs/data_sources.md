# Data sources

Every figure MemberAssist shows comes from one of the sources below, or is
synthetic and labelled so. This file is the human-readable record; the
`data_sources` table is the machine-readable one, written by the fetch scripts
and checked by `tests/test_data_integrity.py`.

**Retrieved:** 2026-10-27 · **Plan scope:** Dallas County, Texas (FIPS 48113),
plan year 2026 · **Rebuild everything:** `uv run python manage.py seed`

---

## The synthetic-data rules

1. **Nobody in this system exists.** Members, claims, accumulators,
   prior authorizations, refills and logins are generated. Every such row
   carries `is_synthetic = true`, and the UI shows a "Synthetic demo data"
   badge at all times.
2. **No real patient data is used anywhere**, in any form, at any stage.
3. Contact details are chosen so they cannot reach a real person: phone
   numbers use the `555-01xx` exchange reserved for fiction, and email
   addresses use the `.invalid` top-level domain reserved by RFC 2606.
4. **Real data is never edited to suit a story.** Plan cost sharing, prices,
   provider records and clinical code titles are used exactly as published.
   Where the demo needs a particular situation, it is built by choosing which
   *synthetic* member and claim to create — never by changing a real number.
5. Raw downloads live in `data/raw/`, which is git-ignored. Nothing fetched
   from CMS, NLM or Synthea is committed to this repository.

---

## Real sources in use

### 1. Plans, benefits and cost sharing — CMS Marketplace Public Use Files

| | |
| --- | --- |
| **Landing page** | <https://www.cms.gov/marketplace/resources/data/public-use-files> |
| **Files used** | `service-area-puf.zip`, `plan-attributes-puf.zip`, `benefits-and-cost-sharing-puf.zip`, plan year 2026 |
| **Base URL** | `https://download.cms.gov/marketplace-puf/2026/` |
| **Retrieved** | 2026-10-27 |
| **Rows stored** | 4 plans, 536 cost-sharing rows |
| **Licence** | US federal government work, public domain. The PUFs are the issuers' own QHP filings as published by CMS, with no redistribution restriction. Issuer and plan marketing names are the issuers' trademarks and are reproduced factually. |
| **Script** | `backend/scripts/fetch_plans.py` · client `backend/app/external/cms_puf.py` |

**Why the PUFs and not the Marketplace API.** The specification prefers
`https://marketplace.api.healthcare.gov/api/v1`, and allows the PUFs when the
key has not arrived. Calling the API without a key returns, verbatim:

```
401 {"message":"No API key found in request","request_id":"a567e800a92db814bd2cf61539042213"}
```

`CMS_MARKETPLACE_API_KEY` has been requested but not issued. The PUFs need no
key and carry the same regulatory filings.

**How the four plans were chosen.** 138 individual medical plans are sold in
Dallas County for 2026. `fetch_plans.py` picks one Bronze (HSA-eligible where
available), one Silver, one Gold with out-of-network coverage, and one
network-restricted HMO or EPO — and requires at least one to price advanced
imaging as coinsurance after the deductible, because the demo and the
cost-estimator tests depend on that design existing. The chosen set, with the
reason for each, is written to `data/mappings/selected_plans.csv`.

**One adaptation worth noting:** the specification suggests a "Gold PPO". No
PPO is offered on the Dallas County exchange for 2026 — only HMO, EPO and POS
— so the Gold pick prefers POS, which is the closest design with
out-of-network coverage.

### 2. Procedure prices — CMS Physician Fee Schedule

| | |
| --- | --- |
| **Landing page** | <https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files> |
| **File used** | `RVU26A` → `https://www.cms.gov/files/zip/rvu26a-updated-12-29-2025.zip`, member `PPRRVU2026_Jan_nonQPP.csv` |
| **Retrieved** | 2026-10-27 |
| **Rows stored** | 38 price rows across 29 codes |
| **Licence** | US federal government work, public domain, **except** the procedure descriptors: *"CPT codes and descriptions only are copyright 2026 American Medical Association. All Rights Reserved."* CMS publishes them inside the public fee-schedule files; MemberAssist reproduces them factually to name a procedure and does not redistribute the CPT code set. |
| **Script** | `backend/scripts/fetch_prices.py` · client `backend/app/external/cms_prices.py` |

**Derivation.** The national Medicare allowed amount follows CMS's own
published formula, with national geographic indices of 1.0:

```
allowed = total RVU × conversion factor
payment = allowed × 0.80      (Medicare Part B pays 80% after the deductible)
```

Worked check, code 73721 (MRI of a knee), 2026 January release:
total non-facility RVU 6.12 × conversion factor 33.4009 = **$204.41**.

Each row stores its own `derivation` string, so any figure can be re-derived.

**Known gap, and why.** The specification names the CMS dataset *"Medicare
Physician & Other Practitioners — by Geography and Service"*, reached through
`https://data.cms.gov/data-api/v1/dataset/<uuid>/data`. That host returns
**HTTP 403 (Akamai "Access Denied")** from this network on every path the
project needs — `/data.json`, `/data-api/*`, `/api/1/metastore/*` and the
dataset landing pages — with any User-Agent, from both `httpx` and `curl`.
Only `/provider-data/*`, a different catalogue holding provider *quality*
measures, responds. `catalog.data.gov`'s CKAN API returns 404, and
`healthdata.gov` does not carry the dataset.

That dataset is the only public source for **average submitted charges**, so
`procedure_prices.avg_submitted_charge` is `NULL`. The consequence is
deliberate and visible: the cost estimator gives a single Medicare-benchmark
estimate rather than a low–high range, and says which it is giving. No
multiplier was invented to manufacture a range. `fetch_prices.py` re-probes
the blocked host on every run and reports if it becomes reachable, at which
point the column can be filled and the range appears automatically.

### 3. Laboratory prices — CMS Clinical Laboratory Fee Schedule

| | |
| --- | --- |
| **Landing page** | <https://www.cms.gov/medicare/payment/fee-schedules/clinical-laboratory-fee-schedule-clfs/files> |
| **File used** | `https://www.cms.gov/files/zip/26clabq4.zip`, member `CLFS 2026 Q4*.csv` |
| **Retrieved** | 2026-10-27 |
| **Rows stored** | 5 codes |
| **Licence** | US federal government work, public domain, except the CPT descriptors (as above). |

**Why a second fee schedule.** The five laboratory codes the project uses
(36415, 80053, 80061, 83036, 85025) carry Physician Fee Schedule status code
**`X` — statutory exclusion**, meaning they are paid under a different
schedule. Without the CLFS they would have had no price at all. Medicare pays
**100%** of the CLFS rate, because clinical diagnostic laboratory tests carry
no Part B coinsurance or deductible.

The linked "CLFS Files" page builds its download list in the browser, so the
quarterly files are addressed directly at the documented
`.../files/zip/{yy}clabq{n}.zip` pattern, newest quarter first.

### 4. Providers — CMS NPI Registry

| | |
| --- | --- |
| **Endpoint** | `https://npiregistry.cms.hhs.gov/api/?version=2.1&taxonomy_description=<specialty>&city=Dallas&state=TX` |
| **Retrieved** | 2026-10-27 |
| **Rows stored** | 64 providers across 8 specialties |
| **Licence** | US federal government work, public domain. Provider names, NPIs and practice addresses are real and already public. MemberAssist adds no personal data to them. |
| **Script** | `backend/scripts/fetch_providers.py` · client `backend/app/external/npi.py` |

**Two corrections the real API forced.**

1. The registry's `city` filter matches *any* address on a record, including
   the mailing address. A Dallas search returned a surgeon practising in
   Minneapolis whose billing address is a Dallas PO box. Every result is now
   re-checked against its `LOCATION` address.
2. The `taxonomy_description` filter likewise matches *any* taxonomy on a
   record, and a provider's **primary** taxonomy is often a different
   specialty — a Radiology search returned a record whose primary taxonomy is
   Neurology. The taxonomy that actually matched the search is used, and
   records that only matched on an unrelated taxonomy are dropped.

**The network flag is ours, not CMS's.** No public dataset says which
providers are inside a given plan's network; that is contract information each
insurer publishes only in its own directory. `providers.in_network` is
therefore assigned by a documented deterministic rule, and
`providers.network_rule` records which rule produced each value:

| Rule | Definition |
| --- | --- |
| **N1** | In network when `sha256(npi)` first byte < 192 — about 75% of providers, a realistic breadth for a county HMO or EPO. |
| **N2** | The lowest NPI in each specialty is forced **out of network**, so every specialty can demonstrate an out-of-network answer and the cross-network tests always have a subject. |

### 5. Clinical code titles — NLM Clinical Tables and MedlinePlus Connect

| | |
| --- | --- |
| **ICD-10-CM** | `https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search?sf=code,name&terms=<code>` |
| **HCPCS Level II** | `https://clinicaltables.nlm.nih.gov/api/hcpcs/v3/search?terms=<code>` |
| **MedlinePlus Connect** | `https://connect.medlineplus.gov/service?mainSearchCriteria.v.cs=2.16.840.1.113883.6.90&mainSearchCriteria.v.c=<code>&knowledgeResponseType=application/json` |
| **Retrieved** | 2026-10-27 |
| **Rows verified** | 66 ICD-10-CM codes |
| **Licence** | Free to use. ICD-10-CM is maintained by CDC/NCHS and is in the public domain in the United States. MedlinePlus content is produced by the US National Library of Medicine. |
| **Rate limit** | 100 requests per minute per IP; responses are cached for 12–24 hours. |
| **Scripts** | `backend/scripts/verify_mappings.py` · clients `backend/app/external/clinical_tables.py`, `medlineplus.py` |

**Every ICD-10-CM code in the mapping is verified against this service, not
trusted.** `verify-mappings` looks each one up, writes the official title back
into `data/mappings/snomed_to_icd10.csv` as `verified_title`, and exits
non-zero if any code fails. The first run rejected four codes, all of which
were wrong:

| Written | Rejected because | Corrected to |
| --- | --- | --- |
| `E88.81` | not a valid code | `E88.810` Metabolic syndrome |
| `T14.8` | needs a 7th character | `T14.8XXA` Other injury of unspecified body region, initial encounter |
| `Z59.1` | restructured into subcategories | `Z59.10` Inadequate housing, unspecified |
| `U07.1` | our own shape filter wrongly excluded U-codes | filter fixed; `U07.1` COVID-19 is valid |

**Coverage limit, and what it changed.** Clinical Tables holds HCPCS **Level
II** codes only (`A4253`, `J1745`, `G0008`). Five-digit **CPT** codes such as
`73721` and `99213` return zero results, because CPT is licensed by the AMA
and NLM does not redistribute it. Procedure descriptions therefore come from
the CMS fee-schedule files instead, where CMS publishes them.

### 6. Member population — Synthea

| | |
| --- | --- |
| **Source** | <https://synthetichealth.github.io/synthea-sample-data/> |
| **File used** | `downloads/10k_synthea_covid19_csv.zip` (10,000 patients, 54 MB) |
| **Retrieved** | 2026-10-27 |
| **Rows stored** | 200 members |
| **Licence** | Apache License 2.0, from The MITRE Corporation. Synthea output is entirely synthetic: no real patient data is involved. The public sample is used unmodified as input. |
| **Script** | `backend/scripts/make_synthetic_data.py` |

Synthea was **not run locally** — Java is not installed on this machine, which
the specification expressly allows. The smaller "latest" sample was rejected:
it holds 111 patients, only 77 of them living adults, too few for the ~200 the
project wants. The 10,000-patient sample has 8,489 living adults aged 18–90.

---

## Transformations applied to the Synthea population

All deterministic from `RANDOM_SEED = 20261027`, so every machine builds the
same database.

| | Transformation | Why |
| --- | --- | --- |
| **T1** | Re-homed to Dallas County, TX: the street line is kept, city/state/ZIP are replaced with real Dallas values. | Synthea's sample is Massachusetts, which runs its own exchange, so its members could not hold a HealthCare.gov plan. |
| **T2** | Phone numbers in the `555-01xx` block; emails at `.invalid`, ASCII-folded. | Neither can reach a real person. Synthea emits no contact details. |
| **T3** | Name digits stripped (`Schiller186` → `Schiller`). | Readability. `is_synthetic` and the UI badge carry the synthetic signal instead. |
| **T4** | Each member's history shifted forward **as a whole**, preserving every interval, so their most recent encounter falls 3–120 days before today. | The sample's encounters end around 2020; claims must sit inside plan year 2026. Intervals are never distorted. |
| **T5** | Synthea encounters mapped to priced HCPCS codes by encounter class and the condition category behind them. Where an outpatient or inpatient encounter has no reason code, the member's own chronic condition supplies the category. | Synthea emits SNOMED encounter and reason codes, not billing codes. Without the fallback every specialist encounter would collapse to a generic office visit and no imaging, surgery or prior authorization would ever appear. |
| **T6** | Billed charge = allowed × factor, factor 1.8–3.2 from a hash of the claim id. | Real claims bill above the allowed amount. **This is the one invented number in a claim** and is recorded as such here and in the generator's docstring. |
| **T7** | One claim in eight uses an out-of-network provider. | Members mostly use their plan's network. Choosing providers uniformly would deny a realistic plan's claims far too often. |

### Claim status rules

Every claim stores the rule that produced its status, in `claims.status_rule`.
Precedence is the order below.

| Status | Rule |
| --- | --- |
| `PA_MISSING` | A prior-authorization-required code with no approved PA. The decision is made **once per member and procedure code**, so a member can never hold an approved knee-MRI authorization and a missing-authorization denial for a knee MRI at the same time. 70% of PA-required services are approved. |
| `OUT_OF_NETWORK` | An HMO or EPO plan plus an out-of-network provider. |
| `NOT_COVERED` | The plan's own filing marks the benefit as not covered. Acupuncture and bariatric surgery are excluded by all four selected plans, which is where these denials come from — real exclusions, not invented ones. |
| `DUPLICATE` | Same member, same code, same service date as an earlier claim. |
| `PENDING` | Service date within the last 21 days; nothing determined yet. |
| `PAID` | Covered; the member's share is calculated by `app/services/cost_sharing.py` against the deductible remaining at that point in the year. |

### What the seeded database contains

| Table | Rows | Real or synthetic |
| --- | --- | --- |
| `plans` | 4 | **Real** CMS filings |
| `plan_benefits` | 536 | **Real** CMS filings |
| `procedure_prices` | 43 | **Real** CMS fee schedules |
| `providers` | 64 | **Real** NPI records; network flag ours (rules N1/N2) |
| `members` | 200 | Synthetic |
| `claims` | 715 | Synthetic (604 paid, 58 denied, 52 pending) |
| `accumulators` | 200 | Synthetic, recomputed from the claims |
| `pa_requests` | 14 | Synthetic |
| `refills` | 336 | Synthetic, from Synthea's RxNorm-coded medications |
| `users` | 5 | Synthetic demo logins |

---

## Mapping files

| File | Contents | Verified by |
| --- | --- | --- |
| `data/mappings/code_to_benefit.csv` | 34 procedure codes → Marketplace benefit category, default setting, whether prior approval is commonly required | `verify-mappings`: every code must have a CMS price row and every benefit name must join to a real `plan_benefits` row |
| `data/mappings/procedure_synonyms.csv` | 80 plain-language phrases → procedure code ("knee MRI" → 73721) | `verify-mappings`: every target code must exist in the code mapping |
| `data/mappings/snomed_to_icd10.csv` | 66 Synthea SNOMED codes → ICD-10-CM, with category, claimability and the official NLM title | `verify-mappings`: every ICD-10 code looked up against NLM Clinical Tables |
| `data/mappings/selected_plans.csv` | The four chosen plans and why each was chosen, with its real SBC URL | written by `fetch-plans` |

Where SNOMED does not state laterality (knee and hip osteoarthritis, knee and
ankle injuries), a side is assigned for the synthetic claim and the choice is
recorded in that row's `mapping_note`.

---

## Sources for later phases

Recorded here now so the plan is visible; not yet called.

| Phase | Source | Endpoint |
| --- | --- | --- |
| 2 | Plan SBC PDFs | `URLForSummaryofBenefitsCoverage` from the Plan Attributes PUF (already captured in `selected_plans.csv`) |
| 2 | CMS/DOL Uniform Glossary, HealthCare.gov glossary and appeals pages | <https://www.healthcare.gov/> |
| 2 | Medicare NCD 220.2 (MRI), Medicare Benefit Policy Manual | <https://www.cms.gov/medicare-coverage-database/> |
| 12 | NPI Registry, by specialty and city | as section 4 above |
| 15 | RxNav (RxNorm normalisation) | `https://rxnav.nlm.nih.gov/REST/rxcui.json?name=<name>` — reachable, verified 2026-10-27 |
| 15 | openFDA drug labels | `https://api.fda.gov/drug/label.json?search=openfda.generic_name:"<name>"&limit=1` — reachable, verified 2026-10-27 |
| 15 | CMS Marketplace API drug coverage | `/drugs/covered` — **needs `CMS_MARKETPLACE_API_KEY`** |

---

## Reproducing all of it

```bash
uv run python manage.py seed        # migrate, then every step below in order
```

| Step | Command | Writes |
| --- | --- | --- |
| 1 | `manage.py migrate` | schema |
| 2 | `manage.py fetch-plans` | `plans`, `plan_benefits`, `selected_plans.csv` |
| 3 | `manage.py fetch-prices` | `procedure_prices` |
| 4 | `manage.py fetch-providers` | `providers` |
| 5 | `manage.py verify-mappings` | verified titles in `snomed_to_icd10.csv` |
| 6 | `manage.py make-data` | `members`, `claims`, `accumulators`, `pa_requests`, `refills`, `users` |
| 7 | `manage.py db-stats` | prints row counts and recorded sources |

Downloads are cached in `data/raw/`, so re-runs do not re-fetch from CMS.
Delete that directory to force a fresh download.
