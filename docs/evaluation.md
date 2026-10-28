# Evaluation

How MemberAssist is measured, what the current numbers are, and which targets are not met yet.

Specification: `MASTER_PROMPT.md` section 15. Live build state: `PROGRESS.md`.

This document is written as the evaluation is built, phase by phase. Phase 2 can measure
retrieval; answer correctness, faithfulness, masking recall and attack blocking need the gateway
and the models that arrive in Phases 3, 4 and 7, and are reported as "not measured yet" rather
than estimated.

---

## Test data and who wrote it

| File | Contents | Author |
| --- | --- | --- |
| `eval/testset.yaml` | 50 questions across coverage, claim status, cost, multi-turn, out-of-documents, attacks and safety | **Questions, categories and users: Person B.** Expected facts, sources, tools and behaviour: Person A |
| `eval/attacks.yaml` | 35 attacks — injection, role-play, prompt extraction, cross-member access, tool-argument smuggling, obfuscation, exfiltration, SQL | **Person B** |
| `eval/benign.yaml` | Benign messages that must *not* be blocked, for measuring false positives | **Person B** |
| `eval/pii_cases.yaml` | 100 strings with planted identifiers, for masking recall | **Person B** |

**Credit.** The four test-data files above were authored by **Person B** and supplied as
`team_inputs/`. They are the basis of the attack, benign, PII and question suites, and are used
unmodified except where noted below. Writing the adversarial set independently of the person
building the guard is the right way round: the guard is not tuned to its own author's
imagination.

### Changes made to the supplied files, and why

| File | Change | Reason |
| --- | --- | --- |
| `attacks.yaml` | **A27, A28, A29** changed from `expected: block` to `expected: refuse` | All three ask for a medication dose. That is a medical-advice refusal through the safety route, not a security event to be blocked before the pipeline runs. Scoring them as blocks would have inflated the attack-blocking figure with cases that are not attacks, and would have hidden a genuine safety requirement inside a security metric. A header in the file now states the distinction. |
| `testset.yaml` | **T35** reclassified from `not_in_plan_documents` to `coverage`, expecting "not covered" with a citation | Verified against all four SBCs: every one lists **"Dental care (Adult)"** under *Services Your Plan Generally Does NOT Cover*. The documents answer this question, so "I could not find it" would be the wrong answer. |
| `testset.yaml` | **T35–T39** `must_handoff` set to `false`, `must_offer_human` set to `true` where applicable | Saying "I could not find this in your plan documents" and offering a person is not the same event as a handoff firing. Separating the two keeps the handoff metric meaningful. |
| `testset.yaml` | **T48, T50** `must_handoff` set to `false`, `expect: emergency_route` | The emergency safety route answers from configuration before any LLM call. It is not a handoff and not a generated answer. |
| `testset.yaml` | **T36** kept as "not found", despite an adjacent exclusion | "LASIK" appears **nowhere** in any of the four plan documents. *Routine eye care (Adult)* is excluded, but that does not settle LASIK, so inferring "not covered" would be guessing. |
| `testset.yaml` | **T37** kept as "not found", with the adjacent exclusion cited | The documents exclude *"Non-emergency care when traveling outside the U.S."*; the question asks about **emergency** care abroad, which the SBC does not address. Citing the exclusion as the answer would be wrong, and so would claiming the documents say nothing at all. This is the test set's one deliberate partial-information case. |
| `faq.md` | Added to the knowledge base as `memberassist_member_faq` after verification; no content changed | Every figure in its timelines table was checked against the HealthCare.gov pages it cites. All nine matched. See `docs/data_sources.md`. |

---

## How retrieval is measured

```bash
uv run python manage.py eval-retrieval              # hit rate
uv run python manage.py eval-retrieval --calibrate  # and sweep the confidence threshold
```

**Hit rate** is: for each question the plan documents should answer, is a chunk from the expected
document *and page* among the top five returned? Only items whose `answered_by` is `rag` or
`rag+tool` are scored — a hit rate is meaningless for a question a database tool answers.

**The expected pages were read out of the documents, not taken from retrieval output.** That
matters: scoring retrieval against whatever retrieval returns measures nothing.

Multi-turn items are scored on their **first** turn. Later turns are deliberately
context-dependent ("What about if I get it out of network?") and the step that rewrites them into
standalone questions is Phase 3, so scoring an unresolved follow-up would be measuring a component
that does not exist.

---

## Current metrics

Measured 2026-10-28 against the real index (15 documents, 219 chunks, chunker v4) and the real
local models, with `bge-small-en-v1.5` for embeddings and `bge-reranker-base` for reranking.

| Metric | Target | Measured | Status |
| --- | --- | --- | --- |
| Retrieval hit rate (right chunk in top 5) | ≥ 90% | **86% (18/21)** | ❌ 3 misses, all one cause — see below |
| Correct "I don't know" | ≥ 90% | **50% (2/4)** | ❌ the reranker score does not separate the classes — see below |
| Answer correctness (judge + manual) | ≥ 85% | not measured | needs the LLM (Phase 3) |
| Faithfulness (RAGAS) | ≥ 0.85 | not measured | needs the LLM (Phase 3) |
| PII masking recall | ≥ 95% | not measured | needs the masker (Phase 3) |
| Attack blocking | 100%, benign FP ≤ 5% | not measured | needs the input guard (Phase 4) |
| Latency p90 | < 5 s | not measured | needs the full pipeline (Phase 3) |
| Intent classifier macro-F1 | reported | not measured | needs the classifier (Phase 4) |

### Why the hit rate is 86% and not higher

All three misses are the same defect, and it is a vocabulary gap rather than a ranking problem.

| Item | Question | What happened |
| --- | --- | --- |
| T03 | "Is physiotherapy covered after a knee injury?" | The plan's rehabilitation row (SBC page 3) **was never a retrieval candidate** |
| T04 | "How many physical therapy visits are covered each year?" | same |
| T33 | "Are physical therapy sessions covered?" (first turn) | same |

The member's Summary of Benefits and Coverage prices this benefit as **"Rehabilitation
services"**. It never uses the words *physiotherapy* or *physical therapy*. BM25 cannot bridge
that, and the embedding does not bridge it strongly enough to put the row in the top 20. Because
the right chunk is not in the candidate pool at all, no amount of reranking or weighting can
recover it — this was confirmed by inspecting the pool directly.

**The fix is already-verified Phase 1 data.** `data/mappings/procedure_synonyms.csv` maps
plain-language phrases to procedure codes and `data/mappings/code_to_benefit.csv` maps codes to
the benefit category a plan prices. Expanding a query through those tables before searching turns
"physiotherapy" into the benefit name the document actually uses. That belongs with the answering
step and the router-first tool pre-fetch, which the specification puts in **Phase 3**, so it is
scheduled there rather than half-built now.

### Why "correct I don't know" is 50%

The specification's confidence check is a threshold on the top rerank score. It was implemented
and calibrated honestly, and the measurement says it does not work well on this corpus.

| | Value |
| --- | --- |
| Fitted threshold | **0.016** |
| Overall | 23/25 correct |
| Answerable questions answered | 21/21 |
| Unanswerable questions refused | **2/4 = 50%** (target ≥ 90%) |
| Lowest-scoring *answerable* question | 0.01602 |
| Highest-scoring *unanswerable* question | 0.07514 ("Will my insurance pay for LASIK eye surgery?") |

The classes overlap, so the threshold cannot be raised to catch the remaining two without
starting to refuse real questions. The cause is that a cross-encoder scores **stylistic
similarity, not answerability**: a fluent glossary paragraph about eye care looks like a good
match for a LASIK question the documents cannot answer, while a terse benefit row that does
answer a question shares almost no wording with it.

The same synonym bridge is the identified fix: a question whose service resolves to a real
procedure code in a priced benefit category is answerable; one that resolves to nothing — LASIK,
veterinary care, hospital wait times — is not. Recorded in `app/rag/confidence.py` with the
measurement, the date, and the command that reproduces it.

Meanwhile the system **errs toward declining**, and every decline offers a human. A false refusal
costs a member a handoff; a confident wrong answer about their coverage costs them money.

---

## Defects this evaluation found

Worth recording, because each was a real bug caught by measuring rather than by reading code.

| Found by | Defect | Fix |
| --- | --- | --- |
| Verifying the teammate FAQ's timelines | The HTML parser enumerated `<p>`/`<li>`, but HealthCare.gov renders explainer paragraphs into bare `<div>`s. The **180-day internal-appeal deadline** and the **45-day external-review decision** were in the fetched HTML and absent from the index. | Walk leaf blocks instead of a tag whitelist. The two appeals pages went from 628 and 451 words to 763 and 715. |
| First `ask-index` run on a knee-MRI question | All 40 retrieval candidates came from one 20,000-word Aetna bulletin on **breast** MRI; the member's own plan row never entered the pool. | Removed that document; filled the specification's commercial-PA slot with Aetna's precertification lists. Added a build-time warning when any shared document exceeds 25% of the index. |
| That build-time warning, immediately | The Medicare Benefit Policy Manual was 53% of the index. | Narrowed the window to section 220 (coverage) and dropped section 230 (provider licensing, which no member asks about). Now 33%. |
| Inspecting the candidate pool for therapy questions | The hero plan's SBC page 3 groups **15 priced benefits into one chunk**, so the rehabilitation row was ~7% of its chunk and unretrievable. | Table chunks now target 160 words rather than 520, so each benefit is a meaningful share of its own chunk. Rows are still never split. |
| First index build's chunk statistics | Six chunks were page navigation — "Back to glossary", "Email this page". | Bidirectional runt merging plus a 12-word floor. Minimum chunk size went from 3 words to 61. |
| Comparing authored and published documents | An explainer we wrote outranked the member's own SBC on 5 of 14 coverage questions. | Removed it. The authoritative source for what a plan pays is the plan's own document. |
| `tests/test_data_integrity.py` (Phase 1 test, still green) | The new `kb_documents` provenance row had a prose note where a URL belonged. | Points at the committed manifest instead. The Phase 1 test was right to insist. |

---

## Test suite

```
uv run python manage.py test     # fast suite: 151 passed, 10 deselected
uv run pytest                    # with slow tests: 161 passed
uv run python manage.py typecheck # mypy: 28 source files, clean
```

Phase 2 added 72 tests: 56 on parsing, chunking, fusion, the per-document cap and the confidence
verdict, and 16 on knowledge-base consistency.

The consistency tests are the ones that matter for trust. They assert that **every
`plan_benefits` number in the database appears in that plan's own SBC text** — the number a tool
calculates with and the number a member can read in their document have to be the same number.
They also pin the facts the test set depends on: that adult dental care really is excluded, that
"LASIK" really is absent from all four documents, and that only *non-emergency* travel care is
excluded.

---

## History

| Date | Phase | Retrieval hit rate | Correct "I don't know" | Notes |
| --- | --- | --- | --- | --- |
| 2026-10-28 | 2 | 86% (18/21) | 50% (2/4) | First measurement. 15 documents, 219 chunks, chunker v4. Both gaps traced to one vocabulary gap with a Phase 3 fix identified. |
