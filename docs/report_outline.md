# MemberAssist Project Report and Presentation Outline

**Project:** MemberAssist — Secure AI Conversational Assistant for Health Insurance Members
**Document status:** Detailed outline; replace every `[RESULT]`, `[INSERT]`, and other bracketed placeholder with verified project-specific information before submission.
**Data policy:** Use synthetic/demo member and claim records only in screenshots, examples, and experiments unless the project’s approved data policy explicitly allows otherwise.

---

# Part A — Detailed Project Report Outline (10–15 pages)

## Suggested report structure and page budget

| Section | Suggested pages |
|---|---:|
| Cover page | 1 |
| Abstract and keywords | 0.5 |
| 1. Introduction and problem statement | 1 |
| 2. Objectives, scope, and requirements | 1 |
| 3. Integration of the three case studies | 1 |
| 4. Proposed system architecture | 1.5 |
| 5. SecureGateway: eight security layers | 2 |
| 6. Retrieval-Augmented Generation (RAG) pipeline | 1.5 |
| 7. Data sources and data handling | 1 |
| 8. Implementation and user workflow | 1 |
| 9. Evaluation methodology and metrics | 1.5 |
| 10. Results and discussion | 1 |
| 11. Limitations, risks, and future work | 1 |
| Conclusion and references | 1 |
| **Total target** | **14–15 pages** |

The final page count will depend on figures, tables, formatting, and the institution’s required template.

## Cover page

Include:
- Project title: **MemberAssist: A Secure AI Conversational Assistant for Health Insurance Members**
- Team members: `[TEAM MEMBER NAMES / IDS]`
- Department and institution: `[DEPARTMENT]`, CBIT
- Course / project / hackathon: `[COURSE OR EVENT]`
- Faculty guide: `[GUIDE NAME]`
- Submission date: `[DATE]`

## Abstract (about 150–250 words)

Summarize:
- The difficulty members face when trying to understand coverage, claims, prior authorization, and expected costs.
- MemberAssist as a conversational interface that combines three related case-study ideas into one product.
- The proposed architecture: React frontend, FastAPI backend, an eight-layer SecureGateway, Gemini with Groq fallback, and tools for plan-document RAG, claims lookup, cost estimation, code explanation, and human handoff.
- The RAG design: hybrid retrieval, reranking, confidence checks, and source citations.
- Security controls: identity and access checks, input and output guards, PII masking, safe tool execution, unmasking, and hash-chained audit logs.
- Evaluation plan and results placeholders: `[RETRIEVAL HIT RATE]`, `[ANSWER CORRECTNESS]`, `[FAITHFULNESS]`, `[ATTACK BLOCKING RATE]`, `[PII MASKING RECALL]`, and `[LATENCY]`.
- State clearly that prototype/demo records are synthetic and that the system is not a substitute for a real insurer, clinician, or official plan documents.

**Keywords:** Conversational agents, health insurance, retrieval-augmented generation, prior authorization, claims analytics, privacy, security gateway.

## 1. Introduction and problem statement (about 1 page)

### 1.1 Background
Explain how health insurance members may need to interpret complex plan documents, understand claim decisions, estimate out-of-pocket costs, and follow prior authorization requests. These tasks can require navigating several documents, portals, and phone support channels.

### 1.2 Problem statement
Describe three user-facing pain points:
1. **Long support waits:** Members may spend time on hold or repeat details when they need answers about coverage or claim status.
2. **Difficult plan documents:** Coverage terms, exclusions, deductibles, coinsurance, and authorization rules can be hard to locate or understand.
3. **Sensitive health and identity data:** Claim and coverage conversations can include personal identifiers and health-related information, so access control, data minimization, and auditable handling are essential.

Frame these as problems the project aims to address, not as quantified findings unless the team has evidence.

### 1.3 Proposed solution
Introduce MemberAssist as a secure conversational interface that answers grounded questions, retrieves only authorized synthetic claim data, estimates costs when required inputs are available, explains terminology, and hands off cases it cannot safely resolve.

### 1.4 Project contributions
List the planned contributions:
- One conversational product integrating three case-study capabilities.
- RAG over plan documents with citations and confidence checks.
- A SecureGateway with eight security layers.
- Tool-based claims lookup and cost estimation.
- A measurable evaluation plan with security and answer-quality metrics.

## 2. Objectives, scope, and requirements (about 1 page)

### 2.1 Objectives
- Make insurance information easier to understand using plain English.
- Ground coverage answers in retrieved plan-document evidence.
- Support authorized claim-status and denial explanations.
- Provide transparent cost estimates from available plan parameters.
- Track or explain prior authorization status when an authorized record is available.
- Reduce unsafe disclosure through layered security controls.
- Escalate uncertain, urgent, or unsupported questions to a human.

### 2.2 Functional requirements
Use a table such as:

| ID | Requirement | Planned implementation |
|---|---|---|
| FR-01 | Ask questions in natural language | React chat interface |
| FR-02 | Answer plan-document questions with evidence | RAG retrieval and citations |
| FR-03 | Show a member’s authorized claim status | Claims database tool with access checks |
| FR-04 | Explain denial terminology and next steps | Claims tool + grounded explanation |
| FR-05 | Estimate member cost | Cost estimator using available plan inputs |
| FR-06 | Explain technical or billing codes | Code-explainer tool with source/context checks |
| FR-07 | Escalate unresolved cases | Human-handoff workflow |
| FR-08 | Record security-relevant events | Hash-chained audit log |

### 2.3 Non-functional requirements
Discuss privacy, least privilege, traceability, understandable responses, latency, reliability, fallback behavior, and accessibility. Add measurable targets only when established: `[LATENCY TARGET]`, `[AVAILABILITY TARGET]`, `[SECURITY TARGET]`.

### 2.4 Scope and exclusions
**In scope:** demo coverage Q&A, authorized synthetic claims, illustrative cost estimates, prior authorization status/next-step explanations, security tests, and human handoff simulation.
**Out of scope:** real insurance decisions, real patient records unless separately authorized, diagnosis, treatment recommendations, medication dosing, guaranteed coverage, and replacing official appeal processes.

## 3. Integrating the three case studies into one product (about 1 page)

Explain that the three case studies are not separate applications; they become connected capabilities inside one conversational product.

### 3.1 Conversational agents — the product
The conversational agent is the main user interface. It interprets the member’s question, identifies the task, calls approved tools, and explains the result in plain language. It should not invent policy terms or claim facts.

### 3.2 Prior authorization automation — answering and tracking approvals
The assistant explains what prior authorization means, retrieves the status of an authorized demo request, summarizes missing information or recorded decisions, and explains possible next steps. Status is only reported when present in the source record. It must not claim to submit or approve an authorization unless that action is actually implemented and verified.

### 3.3 Claims and utilization analytics — explaining claims and costs
The assistant retrieves authorized claim data, explains claim status or denial reason, and uses the cost estimator to show an estimate based on available deductible, copay, coinsurance, allowed amount, and network assumptions. Any estimate should list assumptions and say when information is missing.

### 3.4 Integrated example workflow
1. Member asks: “Why was my MRI claim denied?”
2. Identity and access checks confirm the member’s demo identity and permissions.
3. SecureGateway screens the input and masks PII.
4. The router chooses the claims tool and retrieves only the authorized claim record.
5. RAG retrieves relevant plan/authorization text, if available.
6. The answer explains the recorded denial reason, cites plan sources, and distinguishes facts from possible next steps.
7. If information is insufficient or the member asks for a person, the system invokes handoff.
8. Security events are recorded in the audit log.

Include a sequence diagram: `[INSERT INTEGRATED WORKFLOW DIAGRAM]`.

## 4. Proposed system architecture (about 1.5 pages)

### 4.1 High-level architecture
Show the following flow:

**React app → FastAPI → SecureGateway (8 layers) → Gemini, with Groq fallback → approved tools**

Approved tools:
- RAG over plan documents
- Claims database
- Cost estimator
- Code explainer
- Human handoff

The model must not directly access unrestricted databases or secrets. Tools should be called through validated backend interfaces.

### 4.2 Component responsibilities
- **React app:** chat interface, source display, loading/error states, handoff request, and clear demo notices.
- **FastAPI:** API endpoints, session handling, request validation, authorization integration, tool routing, and response formatting.
- **SecureGateway:** enforces the security controls before and after model/tool operations.
- **Gemini:** primary LLM for language understanding and response generation, subject to the gateway’s policy.
- **Groq fallback:** alternate inference route when configured fallback conditions are met. Apply equivalent security checks to both providers; do not bypass the gateway on fallback.
- **RAG service:** retrieves plan-document passages and returns source metadata.
- **Claims database:** contains synthetic demo member, claim, and authorization records.
- **Cost estimator:** deterministic calculations from explicit plan parameters; it should not rely on the LLM to do unverified arithmetic.
- **Code explainer:** translates supported codes into plain English using an approved reference source or project mapping.
- **Human handoff:** creates a handoff request or clear next step; demo behavior must be labeled as simulated if no live agent integration exists.
- **Audit log:** records security-relevant events without unnecessarily storing raw sensitive text.

### 4.3 Request lifecycle
1. Receive and validate request.
2. Authenticate session and authorize requested data.
3. Apply input screening and PII masking.
4. Build a constrained model request.
5. Retrieve evidence or invoke a permitted tool.
6. Validate tool arguments and tool output.
7. Check generated response for policy, leakage, and unsupported claims.
8. Restore masked values only when permitted and necessary.
9. Return answer with citations, estimate assumptions, or handoff status.
10. Append an audit event.

### 4.4 Architecture diagram
Insert a diagram with trust boundaries and data flows:
`[INSERT ARCHITECTURE DIAGRAM: React → FastAPI → SecureGateway layers → Gemini/Groq → tools → response]`.

## 5. SecureGateway: eight security layers (about 2 pages)

Introduce defense in depth: no single filter is expected to catch every failure. Document the implementation status for each layer as `[IMPLEMENTED / PARTIAL / PLANNED]` and test evidence as `[TEST RESULT]`.

| Layer | Purpose | Example control | Evidence to report |
|---|---|---|---|
| 1. Identity & access | Prevent one member from accessing another member’s records | Authenticate session; enforce member/claim ownership server-side; least privilege | `[UNAUTHORIZED ACCESS TEST RESULT]` |
| 2. Input guard | Detect malicious, irrelevant, or policy-violating requests | Input classification, prompt-injection screening, size/type validation, rate limits | `[INPUT ATTACK BLOCKING RATE]` |
| 3. PII masking | Minimize exposure of identifiers to the LLM | Detect and replace names, member IDs, claim IDs, phone numbers, emails, dates of birth, and other configured identifiers with tokens | `[PII MASKING RECALL]`, `[FALSE POSITIVE RATE]` |
| 4. Locked LLM call | Prevent user content from changing system policy or gaining unrestricted capabilities | Fixed system policy, separated untrusted user content, no secrets in prompts, approved provider/model configuration, equivalent controls for fallback | `[POLICY BYPASS TEST RESULT]` |
| 5. Safe tool execution | Stop unauthorized or unsafe tool actions | Tool allowlist, schema validation, server-side authorization, argument normalization, timeouts, read-only defaults | `[TOOL AUTHORIZATION TEST RESULT]` |
| 6. Output guard | Block leakage, unsupported claims, unsafe medical advice, and ungrounded answers | Check policy compliance, validate citations, reject cross-member data, route low-confidence answers to clarification/handoff | `[OUTPUT LEAKAGE TEST RESULT]` |
| 7. Unmask | Restore masked values only when necessary for an authorized response | Token map scoped to a request; verify authorization before restoration; never restore another member’s data | `[UNMASKING TEST RESULT]` |
| 8. Hash-chained audit log | Make changes to logged events detectable | Canonical event serialization; each record includes prior record hash; protect log access and key material | `[LOG INTEGRITY TEST RESULT]` |

### 5.1 Threat model
Cover:
- Prompt injection and attempts to override system instructions.
- Cross-member record access and insecure direct object references.
- PII exposure to model providers or in generated output.
- Malicious or malformed tool arguments.
- Hallucinated coverage statements or citations.
- Fallback-provider security gaps.
- Audit-log tampering or accidental storage of sensitive text.

### 5.2 Security design decisions
Explain least privilege, server-side authorization, separation of policy from user input, minimum necessary data, provider parity, safe failure, and human escalation.

### 5.3 Audit event design
Possible fields: timestamp, request ID, pseudonymous session/member reference, action/tool name, authorization outcome, guard decisions, source IDs, response outcome, previous hash, and current hash. Avoid logging full raw prompts, full medical details, credentials, or unmasked PII unless an approved need and protection model exists.

### 5.4 Security testing
Describe attack cases from `attacks.yaml`, benign false-positive cases from `benign.yaml`, and synthetic PII cases from `pii_cases.yaml`. Report counts and outcomes using placeholders rather than invented values.

## 6. Retrieval-Augmented Generation (RAG) pipeline (about 1.5 pages)

### 6.1 Document preparation
Describe approved plan-document ingestion: extract text, preserve section/page metadata, split into chunks, normalize, and index. Track document version, effective date, and source identifier. Do not use public FAQ text as proof of an individual member’s benefits.

### 6.2 Hybrid search
Combine lexical search (for exact terms, section numbers, and plan wording) with semantic vector search (for paraphrased questions). Merge and deduplicate candidates. Add implementation details: `[LEXICAL ENGINE]`, `[EMBEDDING MODEL]`, `[CHUNK SIZE / OVERLAP]`.

### 6.3 Reranking
Rerank retrieved passages for relevance to the member’s question. Keep source metadata attached throughout. Record the top-k configuration as `[TOP-K]`.

### 6.4 Confidence check
Use retrieval quality, source coverage, and answer support to decide whether the system can answer. If evidence is missing, contradictory, stale, or below the chosen threshold, ask a clarifying question, say the information could not be verified, or hand off. Threshold: `[CONFIDENCE THRESHOLD]`.

### 6.5 Grounded answer and citations
Generate the answer from retrieved evidence. Display source title and section/page where available. Verify that each citation supports the sentence it accompanies. Do not fabricate sources or treat model memory as plan evidence.

### 6.6 RAG evaluation
Test retrieval hit rate, citation precision, answer correctness, faithfulness, abstention behavior, and latency. Include examples of successful retrieval and a deliberately unsupported question: `[INSERT EXAMPLES]`.

### 6.7 RAG diagram
`Question → query normalization → lexical + vector search → merge/deduplicate → reranker → confidence gate → grounded generation → citation validation → response/handoff`.

## 7. Data sources and data handling (about 1 page)

Describe the intended source families and their roles. Confirm actual API access, licensing/terms, data fields, and current availability before claiming they are implemented.

| Source | Intended role | Important boundary |
|---|---|---|
| CMS Marketplace API / Marketplace plan data | Explore available Marketplace plan/coverage information where permitted | Do not imply that public plan data establishes an individual member’s benefits or claim outcome |
| CMS Medicare payment data | Reference payment/allowed-amount context for supported cost explanations or analysis | Medicare payment data is not automatically the allowed amount for a Marketplace member |
| NLM Clinical Tables | Explain supported medical/billing terminology or code lookups when appropriate | A code lookup does not provide a diagnosis or determine coverage |
| MedlinePlus | Plain-language background for health terms | Not a substitute for personalized medical advice; avoid using it to diagnose or prescribe |
| Synthea synthetic patients | Generate synthetic patient-like records for development and testing | Synthetic data only; clearly label all demo records and do not present them as real members |

### 7.1 Data flow and privacy
Show which fields are stored, masked, retrieved, sent to model providers, and logged. Use only the minimum data required for a task. Apply access checks before retrieval, not just before displaying a response.

### 7.2 Synthetic demo dataset
Document generated member, plan, claim, and prior-authorization fields: `[FIELDS]`, `[RECORD COUNT]`, `[GENERATION METHOD]`. State that all identities and claims in the demo are fictitious.

## 8. Implementation and user workflow (about 1 page)

### 8.1 Technology stack
- Frontend: React
- Backend API: FastAPI
- LLM: Gemini primary; Groq fallback
- Retrieval: `[VECTOR STORE]`, `[LEXICAL SEARCH COMPONENT]`, `[RERANKER]`
- Storage: `[DATABASE]`
- Logging: hash-chained audit records
- Deployment: `[LOCAL / CLOUD / CONTAINER SETUP]`

Only list technologies that are actually used. Replace placeholders with the real implementation.

### 8.2 Key workflows
1. Coverage question with source citations.
2. Claim status and denial explanation.
3. Cost estimate with assumptions.
4. Prior authorization status and next steps.
5. Cross-member access attempt blocked.
6. Low-confidence answer handed off.

### 8.3 Screenshots
Add labeled screenshots: `[INSERT CHAT UI]`, `[INSERT CITED ANSWER]`, `[INSERT CLAIM DETAIL]`, `[INSERT COST ESTIMATE]`, `[INSERT HANDOFF]`. Use synthetic data only.

## 9. Evaluation methodology and metrics (about 1.5 pages)

Describe the test environment, model/provider configuration, dataset split, repeated runs if applicable, and scoring method. Include test versions and record the conditions under which each measurement was collected.

| Metric | What it measures | Suggested calculation / method | Result |
|---|---|---|---|
| Retrieval hit rate | Whether retrieval returns a relevant expected source | Queries with at least one expected source in top-k ÷ evaluated queries | `[RESULT]%` |
| Answer correctness | Whether key facts in the answer match the reference answer | Human rubric or fact-level scoring against expected facts | `[RESULT]%` |
| Faithfulness | Whether answer claims are supported by retrieved evidence | Supported factual claims ÷ factual claims reviewed | `[RESULT]%` |
| Attack blocking | Whether labeled malicious test inputs are safely blocked or constrained | Correctly blocked attacks ÷ attacks tested | `[RESULT]%` |
| PII masking recall | How much annotated PII is detected and masked | Correctly masked PII items ÷ all annotated PII items | `[RESULT]%` |
| Latency | Time from request to final answer or handoff | Report median and p95; specify environment and sample size | `[MEDIAN] ms / [P95] ms` |

### 9.1 Test suites
- `attacks.yaml`: attack detection and safe refusal/blocking.
- `benign.yaml`: normal member requests and false-positive checks.
- `pii_cases.yaml`: masking recall and false positives using synthetic examples.
- `testset.yaml`: end-to-end answer, grounding, access control, cost, handoff, and safety tests.

### 9.2 Test procedure
1. Fix and record the model/provider, prompts, retrieval configuration, and dataset version.
2. Run each test suite with the same configuration.
3. Save expected and actual behavior, tool calls, citations, latency, and guard decisions.
4. Score with a documented rubric; have a second reviewer check a sample if possible.
5. Investigate failures and rerun regression tests after changes.
6. Report sample size and limitations with every result.

### 9.3 Results table
| Metric | Baseline | Final system | Notes |
|---|---:|---:|---|
| Retrieval hit rate | `[RESULT]` | `[RESULT]` | `[NOTES]` |
| Answer correctness | `[RESULT]` | `[RESULT]` | `[NOTES]` |
| Faithfulness | `[RESULT]` | `[RESULT]` | `[NOTES]` |
| Attack blocking | `[RESULT]` | `[RESULT]` | `[NOTES]` |
| PII masking recall | `[RESULT]` | `[RESULT]` | `[NOTES]` |
| Median / p95 latency | `[RESULT]` | `[RESULT]` | `[NOTES]` |

Do not invent performance results. If evaluation has not been run, label the table “planned evaluation” rather than presenting placeholders as measured findings.

## 10. Results and discussion (about 1 page)

After running tests, discuss:
- Which question types were answered reliably and which needed clarification or handoff.
- Whether citations supported the generated claims.
- Which attack categories were blocked and where failures occurred.
- PII masking misses and false positives.
- Latency differences between Gemini and Groq fallback.
- Cost-estimator assumptions and cases where data was insufficient.
- Trade-offs between answer coverage, safety, and abstention.

Use evidence: `[INSERT RESULT TABLE]`, `[INSERT FAILURE EXAMPLE]`, `[INSERT SUCCESS EXAMPLE]`. Separate measured observations from hypotheses.

## 11. Limitations, risks, and future work (about 1 page)

### 11.1 Limitations
- Synthetic records do not represent the complexity of real claims, plans, or authorization workflows.
- Public datasets may not match a specific insurer’s contracted rates, plan language, or benefit configuration.
- Retrieval may miss relevant clauses or retrieve outdated/irrelevant passages.
- LLM output may still be incorrect despite guards and citations.
- PII detectors may miss unusual formats or over-mask ordinary text.
- Hash chaining can reveal certain changes but does not by itself prevent deletion, prove event truth, or replace protected storage and key management.
- Provider outages, rate limits, model changes, and latency can affect behavior.
- Live agent transfer, insurer-system integration, and real-time authorization updates may be simulated or not implemented.

### 11.2 Future work
- Evaluate with a broader, reviewed set of plan documents and adversarial examples.
- Add document versioning, effective-date filtering, and stronger citation validation.
- Improve multilingual and accessibility support.
- Add a real handoff integration with consent and minimal-data transfer.
- Improve monitoring, key management, log retention, and tamper-evidence verification.
- Compare primary and fallback models using the same safety and quality tests.
- Run usability testing with clear consent and appropriate institutional review.
- Add more robust cost estimation with plan-specific allowed amounts and transparent assumptions.
- Conduct independent security review and privacy assessment before any real-world use.

## 12. Conclusion (about half a page)

Restate the problem, the unified product approach, the architecture, the eight security layers, and the RAG pipeline. Summarize actual results only after testing. End with the intended value: clearer explanations and safer navigation of insurance information—not replacing insurers, clinicians, or official appeal processes.

## References

Use a consistent citation style required by the department. Include official documentation for each external dataset/API and model, plus the specific Healthcare.gov pages used in the FAQ. Add access dates where required. Do not cite sources that were not actually consulted.

## Appendices (if page limit permits)

- Appendix A: Architecture and sequence diagrams.
- Appendix B: Security-layer detail and threat model.
- Appendix C: Test-suite schema and representative synthetic test cases.
- Appendix D: Evaluation rubric and raw summary tables.
- Appendix E: Example RAG answer with citations.
- Appendix F: Data dictionary and synthetic-data statement.

---

# Part B — 12-Slide Presentation Outline

Target presentation length: approximately 8–12 minutes. Keep slide text concise; use diagrams and one or two examples instead of paragraphs. Replace bracketed placeholders with verified values.

## Slide 1 — Title
**MemberAssist: A Secure AI Assistant for Health Insurance Members**
- Team: `[TEAM NAME / MEMBERS]`
- Department: `[DEPARTMENT]`, CBIT
- Guide / event: `[GUIDE OR EVENT]`
- One-line promise: “Understand coverage, claims, and costs with grounded answers and layered safeguards.”
**Visual:** Product logo or simple chat + shield illustration.

## Slide 2 — The Problem
- Members may wait on hold for routine questions.
- Plan documents can be lengthy and difficult to interpret.
- Claims, prior authorization, and cost-sharing can be confusing.
- Health and identity data require careful protection.
**Visual:** Three pain-point cards: waiting, complex documents, sensitive data.
**Speaker note:** Present these as motivating problems; do not add unsupported statistics.

## Slide 3 — One Product, Three Case Studies
- **Conversational agents = the product:** one natural-language entry point.
- **Prior authorization automation:** explain and track recorded authorization status.
- **Claims & utilization analytics:** explain claims, denials, and estimated member costs.
- One workflow, shared security controls, clear escalation.
**Visual:** Three capability blocks converging into MemberAssist.

## Slide 4 — What MemberAssist Does
- Answers plan questions with citations.
- Retrieves an authorized member’s synthetic claim status.
- Explains denial terminology and possible next steps.
- Estimates costs from explicit plan inputs.
- Explains supported codes and hands off unresolved cases.
**Guardrail banner:** No diagnosis, medical advice, medication doses, or guaranteed coverage decisions.
**Visual:** Example chat mock-up using fake member/claim details.

## Slide 5 — System Architecture
**Flow:** React app → FastAPI → SecureGateway (8 layers) → Gemini (primary) / Groq (fallback) → approved tools.
**Tools:** RAG over plan documents; claims database; cost estimator; code explainer; human handoff.
- Tools are allowlisted and validated.
- Fallback follows the same security controls.
- Model cannot directly query unrestricted records.
**Visual:** Architecture diagram with trust boundaries and arrows.

## Slide 6 — SecureGateway: Eight Layers
1. Identity & access
2. Input guard
3. PII masking
4. Locked LLM call
5. Safe tool execution
6. Output guard
7. Unmask
8. Hash-chained audit log
**Visual:** Eight-step pipeline or layered shield.
**Speaker note:** Briefly explain that each layer addresses a different failure mode; no single layer is sufficient.

## Slide 7 — RAG: Grounded Answers
1. Ingest and chunk approved plan documents.
2. Run hybrid lexical + semantic search.
3. Merge candidates and rerank.
4. Check confidence and source support.
5. Generate a plain-English answer with citations.
6. Abstain, clarify, or hand off when evidence is weak.
**Visual:** Question → retrieval → reranking → confidence gate → cited answer.
**Metric callout:** Retrieval hit rate `[RESULT]`; faithfulness `[RESULT]`.

## Slide 8 — Data Sources and Synthetic Data
- CMS Marketplace API / Marketplace plan data: plan-related public information where applicable.
- CMS Medicare payment data: reference payment context, not a substitute for a member’s plan rates.
- NLM Clinical Tables: supported terminology/code lookups.
- MedlinePlus: plain-language health information, not personalized advice.
- Synthea: synthetic patient-like records for demo and testing.
**Important:** All demo member, claim, and authorization records are synthetic.
**Visual:** Source icons feeding approved data stores.
**Speaker note:** Clarify which sources are implemented versus planned: `[IMPLEMENTED SOURCES]`.

## Slide 9 — Example End-to-End Workflow
**Example:** “Why was my MRI claim denied?”
1. Verify session and member authorization.
2. Screen input and mask identifiers.
3. Retrieve only the authorized synthetic claim.
4. Retrieve relevant plan/authorization passages.
5. Explain the recorded reason and cite evidence.
6. Offer appeal information or handoff when appropriate.
**Visual:** Numbered sequence or swimlane.
**Demo data:** Member `[M10234]`, claim `[CLM-4521]` (synthetic).

## Slide 10 — Evaluation Plan and Results
Show metrics:
- Retrieval hit rate: `[RESULT]%`
- Answer correctness: `[RESULT]%`
- Faithfulness: `[RESULT]%`
- Attack blocking: `[RESULT]%`
- PII masking recall: `[RESULT]%`
- Median / p95 latency: `[RESULT]`
**Test suites:** `attacks.yaml`, `benign.yaml`, `pii_cases.yaml`, `testset.yaml`.
**Visual:** Compact scorecard or bar chart after measurements exist.
**Important:** Until testing is complete, label values “planned” or “not yet measured”; never invent results.

## Slide 11 — Limitations, Risks, and Future Work
**Limitations**
- Synthetic data and simplified workflows.
- Public data may not match actual member benefits or contracted rates.
- RAG and LLM answers can still be wrong.
- Live insurer/agent integration may be simulated.
**Future work**
- Broader plan-document evaluation and stronger citation checks.
- More adversarial and privacy testing.
- Better monitoring, access controls, and fallback evaluation.
- Usability and accessibility testing.
**Visual:** Two-column “Now / Next” layout.

## Slide 12 — Conclusion and Q&A
- One conversational product unifies three case-study capabilities.
- RAG makes answers evidence-based and traceable.
- Eight security layers reduce risks to member data and tool execution.
- Evaluation will quantify quality, safety, privacy, and speed.
- MemberAssist is a demo—not an insurer, clinician, or real claims system.
**Closing:** “Questions?”
**Footer:** `[TEAM CONTACT / PROJECT REPOSITORY]`

---

# Finalization checklist

- [ ] Replace all placeholders with verified project information.
- [ ] Clearly label implemented features versus proposed features.
- [ ] Do not report unmeasured metrics as results.
- [ ] Ensure every architecture diagram matches the actual code.
- [ ] Use only synthetic member/claim data in screenshots and examples.
- [ ] Verify all dataset/API terms, source URLs, and access dates.
- [ ] Check that the report is 10–15 pages after formatting.
- [ ] Keep the presentation to exactly 12 slides.
- [ ] Rehearse a short explanation of each security layer and the end-to-end workflow.
