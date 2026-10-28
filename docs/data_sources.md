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

### 7. Knowledge base — plan documents, federal glossary and policy, one insurer policy

Fetched by `backend/scripts/fetch_kb.py`, catalogued in `backend/app/rag/kb_sources.py`, and
recorded document by document in **`data/kb/manifest.yaml`** with a SHA-256 of the bytes that
were actually downloaded. The manifest is generated, never hand-edited: `retrieved_on` and the
hash are facts about a download that happened, so the script that downloaded it writes them.

The documents themselves live in `data/kb/files/`, which is git-ignored. The manifest is the
committed record of what was fetched, and the hash makes a changed document detectable.

| | |
| --- | --- |
| **Documents** | 15 |
| **Retrieved** | 2026-10-28 |
| **Manifest** | `data/kb/manifest.yaml` |
| **Script** | `backend/scripts/fetch_kb.py` · catalogue `backend/app/rag/kb_sources.py` |
| **Command** | `uv run python manage.py fetch-kb` |

#### The four plan documents

The real Summary of Benefits and Coverage PDF for each plan we sell. The URLs are the
`URLForSummaryofBenefitsCoverage` values that `fetch_plans` captured from the CMS Plan Attributes
PUF into `data/mappings/selected_plans.csv`; the plan name and issuer are read back from the
`plans` table. So the chain runs federal filing → database → knowledge base with nothing retyped.

| Plan | Issuer | Pages | Document |
| --- | --- | --- | --- |
| `47501TX0040002` | WellPoint | 7 | Wellpoint Essential Bronze 4000 HSA (+ Incentives) — the hero member's plan |
| `47501TX0030007` | WellPoint | 7 | Wellpoint Essential Gold POS 700 |
| `33602TX0461124` | Blue Cross and Blue Shield of Texas | 10 | MyBlue Health Gold 403 |
| `33602TX0870174` | Blue Cross and Blue Shield of Texas | 10 | Blue Advantage Plus Silver 202 |

**Licence.** Published by each issuer as the federally required Summary of Benefits and Coverage
for a plan sold on the Marketplace. Copyright of the issuer; quoted and cited for the plan it
describes, not redistributed — which is why `data/kb/files/` is git-ignored.

All four carry `plan_id` set to the real plan, and retrieval filters on it, so one member can
never be shown another plan's cost sharing. `tests/test_rag_retrieval.py` asserts that filter.

#### The eleven shared documents

Every one carries `plan_id = "ALL"`.

| doc_id | Source | Publisher | Licence |
| --- | --- | --- | --- |
| `cms_uniform_glossary` | [Uniform Glossary of Coverage and Medical Terms](https://www.cms.gov/CCIIO/Resources/Forms-Reports-and-Other-Resources/Downloads/uniform-glossary-final.pdf) (PDF, 4 pages) | CMS / US Department of Labor | US federal government work, public domain |
| `hcgov_glossary_deductible` | [Deductible](https://www.healthcare.gov/glossary/deductible/) | HealthCare.gov (CMS) | US federal government work, public domain |
| `hcgov_glossary_copayment` | [Copayment](https://www.healthcare.gov/glossary/co-payment/) | HealthCare.gov (CMS) | as above |
| `hcgov_glossary_coinsurance` | [Coinsurance](https://www.healthcare.gov/glossary/co-insurance/) | HealthCare.gov (CMS) | as above |
| `hcgov_glossary_oop_maximum` | [Out-of-pocket maximum](https://www.healthcare.gov/glossary/out-of-pocket-maximum-limit/) | HealthCare.gov (CMS) | as above |
| `hcgov_appeals_internal` | [Internal appeals](https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/) | HealthCare.gov (CMS) | as above |
| `hcgov_appeals_external` | [External review](https://www.healthcare.gov/appeal-insurance-company-decision/external-review/) | HealthCare.gov (CMS) | as above |
| `medicare_ncd_220_2_mri` | [NCD 220.2 — Magnetic Resonance Imaging](https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=177) | CMS Medicare Coverage Database | as above |
| `medicare_bpm_ch15_therapy` | [Benefit Policy Manual, Chapter 15](https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c15.pdf), **pages 145–190 only** | CMS | as above |
| `aetna_precertification_lists` | [Precertification lists for commercial plans](https://www.aetna.com/health-care-professionals/precertification/precertification-lists.html) | Aetna Inc. | Publicly posted by Aetna. Copyright of Aetna; quoted and cited as an example of a commercial prior-authorization policy, not redistributed |
| `memberassist_member_faq` | `data/kb/authored/member_faq.md` | MemberAssist team (Person B) | Ours. **Marked `generated: true`** in the manifest |

#### Why the Benefit Policy Manual is only pages 145–190

The chapter is 311 pages and almost none of it is about outpatient therapy. The window is located
at fetch time from the chapter's own section headings (`locate_therapy_pages()` in
`fetch_kb.py`), not hard-coded, so a CMS revision that moves the sections is followed rather than
silently mis-indexed. The window runs from section **220, "Coverage of Outpatient Rehabilitation
Therapy Services"**, to the page before section **230, "Practice of Physical Therapy…"**.

Section 230 was deliberately excluded. It governs who is qualified to furnish therapy and under
whose supervision — provider licensing, not member coverage — and no member question reaches for
it. Including it also made the chapter 53% of the whole index, which the build's own dominance
warning flagged.

#### Two documents that were fetched, indexed, and then removed

Recorded because the reasons are measurements, not opinions.

| Document | Why it was removed |
| --- | --- |
| [Aetna Clinical Policy Bulletin 0105 — MRI of the Breast](https://www.aetna.com/cpb/medical/data/100_199/0105.html) | Twenty thousand words of MRI clinical criteria. On "Is a knee MRI covered?" **all 20 vector hits and all 20 BM25 hits came from this one document**, so the member's own plan row never entered the candidate pool. Its slot — the specification's commercial advanced-imaging prior-authorization policy — is filled by Aetna's precertification lists, which is actually about prior authorization and is proportionate at 367 words. |
| An authored "plan basics" explainer we wrote | A generic explainer we wrote outranked the member's own Summary of Benefits and Coverage on 5 of 14 coverage questions. The authoritative source for what a plan pays is the plan's document, so the explainer was removed rather than left to shadow it. |

#### Sources that could not be used

| Source | Status |
| --- | --- |
| eviCore advanced-imaging guidelines | Archive PDF URLs return **404**; the current guidelines index renders its document links with JavaScript, so there is no stable public PDF to cite. Aetna is used instead. |
| Aetna CPB 0006 | **404** at the path published in older indexes. |
| BCBSTX provider prior-authorization list | **404** at `bcbstx.com/docs/provider/tx/clinical/prior-auth/prior-auth-ind-exchange.pdf`. |

---

### Verification of the teammate-authored FAQ, 2026-10-28

`data/kb/authored/member_faq.md` was written by Person B. Before it was added to the knowledge
base, every figure in its section 5 timelines table was checked against the live HealthCare.gov
pages the document itself cites. **All nine rows matched the published wording. Nothing was
corrected.**

| Claim in the FAQ | Verified | Page | The published wording |
| --- | --- | --- | --- |
| Insurer responds to a prior-authorization request: 15 days | ✅ | internal appeals | "Within 15 days if you're seeking prior authorization for a treatment" |
| Insurer responds about services already received: 30 days | ✅ | internal appeals | "Within 30 days for medical services already received" |
| Insurer responds in an urgent-care case: 72 hours | ✅ | internal appeals | "Within 72 hours for urgent care cases" |
| Deadline to file an internal appeal: 180 days (6 months) | ✅ | internal appeals | "You must file your internal appeal within 180 days (6 months) of receiving notice that your claim was denied" |
| Internal appeal decision, care not yet received: 30 days | ✅ | internal appeals | "must be completed within 30 days if your appeal is for a service you haven't received yet" |
| Internal appeal decision, care already received: 60 days | ✅ | internal appeals | "within 60 days if your appeal is for a service you've already received" |
| Deadline to request external review: 4 months | ✅ | external review | "within 4 months after the date you receive a notice or final determination" |
| Standard external review decision: no later than 45 days | ✅ | external review | "Standard external reviews are decided as soon as possible — no later than 45 days after the request was received" |
| Expedited external review decision: no later than 72 hours | ✅ | external review | "Expedited external reviews are decided as soon as possible — no later than 72 hours, or less, depending on the medical urgency of the case" |

Pages checked: <https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/>
and <https://www.healthcare.gov/appeal-insurance-company-decision/external-review/>.

**The check found a defect in our own parser, not in the FAQ.** Two of these facts — the 180-day
filing deadline and the 45-day external-review decision — were present in the fetched HTML but
missing from the parsed text, because HealthCare.gov renders those paragraphs into bare `<div>`
elements and the parser was enumerating `<p>`/`<li>`. It now walks leaf blocks instead. The
appeals pages went from 628 and 451 words to 763 and 715. Without the FAQ check, the knowledge
base would have been silently missing the single most-asked appeal deadline.
`tests/test_rag_parsing.py::TestHtmlParsing::test_text_in_a_bare_div_is_extracted` pins it.

---

### Cross-check against the live CMS Marketplace API — attempted 2026-10-28, blocked

The plans in section 1 come from the CMS Marketplace **Public Use Files**, which are the issuers'
own QHP filings. They should therefore agree with the Marketplace API, and that claim is now
checkable rather than asserted: `backend/scripts/verify_plans_api.py` compares every stored plan's
name, issuer, metal level, plan type, individual deductible, individual out-of-pocket maximum and
HSA flag against the live API, prints the HIOS plan ids and every difference, and with `--apply`
corrects only the fields that differ.

**It has not been able to run.** `CMS_MARKETPLACE_API_KEY` in `.env` has a zero-length value, and
one live call confirms what that means:

```
POST https://marketplace.api.healthcare.gov/api/v1/plans/search
  -> HTTP 401
     {"message":"No API key found in request",
      "request_id":"5b29da4fed7df27c6dcf69329fd40144"}
```

So no HIOS ids or differences are recorded here yet, because none have been observed. This section
will carry the comparison table once a key is saved. Blocker B3 in `PROGRESS.md` tracks it; keys
expire every 60 days.

The Public Use File path stays the fallback regardless, so the stored plan terms are real CMS
filings whether or not the API is ever reachable.

---

## Sources for later phases

Recorded here now so the plan is visible; not yet called.

| Phase | Source | Endpoint |
| --- | --- | --- |
| ~~2~~ | ~~Plan SBC PDFs, glossaries, appeals, Medicare policy~~ | **Done 2026-10-28 — see section 7 above.** |
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
| 8 | `manage.py fetch-kb` | `data/kb/files/`, `data/kb/manifest.yaml`, the `kb_documents` source row |
| 9 | `manage.py build-index` | `data/index/chroma/`, `data/index/bm25.pkl`, `data/index/index_manifest.json` |

Downloads are cached in `data/raw/`, so re-runs do not re-fetch from CMS.
Delete that directory to force a fresh download.
