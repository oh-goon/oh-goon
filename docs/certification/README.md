# Repository understanding certification

This directory turns Oh-Goon's ownership rules, identity rules, lifecycle contracts, execution flows, and change-impact invariants into a version-bound repository-understanding certification for coding agents.

The question is not merely whether an agent can write code. The question is whether it demonstrably understands this repository well enough to be trusted with implementation work.

The certification produces qualification evidence. It is **not** itself a filesystem sandbox, permission system, or write-authority grant.

## Canonical principles

- Repository access does not imply repository understanding.
- Evidence decides. Models judge. Policy governs.
- The LLM produces grading observations, not grading authority.
- Score is continuous; qualification is contractual.
- Missing knowledge is not the same as false knowledge.
- Confident contradiction is worse than explicit uncertainty.
- Evidence must support a claim, not merely exist.
- A caller is not automatically an owner.
- A test proves behavior; it does not necessarily own behavior.
- Weak critical understanding must not be hidden by strong averages.
- Question quality is part of evaluation quality.
- The SECRET reference can be wrong when repository evidence proves otherwise.
- Do not only grade independent answers; grade the inferred mental model.

## Information classes

Every exam question has three distinct information classes:

```text
PUBLIC    candidate may read
EVIDENCE  candidate may discover through repository
SECRET    grading authority only
```

`questions/*.md` and `questions/question-metadata.yml` are PUBLIC. Repository source, tests, schemas, contracts, configuration, and repository-native documentation are EVIDENCE. `answers/*.yml` is SECRET and contains semantic expected claims, contradiction rules, misconception identities, evidence expectations, and criticality.

The authored bank contains **219 questions in 11 sections**. Of these, **157 are active score-bearing qualification questions** and **62 are SHADOW non-score-bearing questions** used for real-world misconception coverage and calibration before promotion. PUBLIC metadata declares question type, dimension, difficulty, broad evidence scope, and the candidate response contract without exposing answer paths or line references.

## Roles and authority

Do not merge these roles merely because one model can technically perform all of them.

1. **Exam-material Goon** validates PUBLIC/EVIDENCE/SECRET material and retains the canonical exam manifest. It never invokes an LLM.
2. **Candidate agent** receives an answer-free repository view and answers from repository evidence.
3. **Semantic grader** reads the frozen candidate submission plus SECRET and emits structured observations: claim states, contradiction observations, evidence judgments, normalized relationship assertions, and confidence.
4. **Oh-Goon scoring policy** is the authority that converts those observations into dimension scores, critical failures, composite understanding, and qualification.

A self-graded run is valid only by sequencing: candidate output is frozen first; only then may a grader see SECRET. The grader never supplies an authoritative final score.

## 1. Verify exam material

From a dependency-complete source checkout:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam verify
```

The shipped verification Plan runs the same exam-material verification through normal Fleet execution:

```bash
uv run --locked --project backend oh-goon plan run \
  templates/plans/oh-goon/repository-understanding-certification/plan.yml \
  --repository-id <oh-goon-repository-id>
```

Canonical `make init` materializes dependencies before verification. The Plan then runs the atomic `oh-goon/repository-exam` Goon against that initialized repository and retains `repository-understanding-certification-manifest.json` as evidence; it does not reacquire dependencies.

## 2. Prepare the blind candidate checkout

```bash
uv run --locked --project backend \
  oh-goon-repository-understanding-exam prepare /tmp/oh-goon-agent-exam
```

The prepared checkout physically excludes `docs/certification/answers/`. The helper also removes transient/install/runtime directories and writes `.oh-goon-certification-exam.json` with non-secret exam metadata.

`prepare` is not an OS sandbox. The caller must expose only the prepared destination and enforce read-only source capability separately. Do not replace a missing capability boundary with a prompt such as “do not edit.”

Candidate responses may use natural language. They should establish:

```text
Answer
Claims
Evidence
Uncertainty
```

A candidate may explicitly state `INSUFFICIENT_EVIDENCE`. That is not automatically a failure.

## 3. Freeze the submission, then create grader observations

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam grade-template \
  --submission /tmp/junior-agent-answers.md \
  --candidate junior-agent \
  --mode normal \
  --candidate-configuration /tmp/junior-agent-configuration.json \
  --candidate-run-identity run-01 \
  --profile change-author.v1 \
  --output /tmp/junior-agent-grades.json
```

The grader artifact is bound to the exact candidate submission, a frozen exam snapshot, and an explicit authority/risk qualification profile. For repeated-run qualification, the harness also supplies an opaque per-execution `candidate_run_identity`; it is not a score and must be unique for each independent run. It embeds enough repository evidence metadata, SECRET semantics, candidate content, profile identity, and version identities for offline regrading. Candidate/model labels never select thresholds.

For every expected claim, the grader chooses exactly one canonical state:

```text
SUPPORTED
PARTIAL
MISSING
UNSUPPORTED
CONTRADICTED
INSUFFICIENT_EVIDENCE
```

These states intentionally distinguish omission, explicit uncertainty, unsupported invention, and semantic contradiction.

The grader also records:

- `question_status`: valid, ambiguous, invalid, or insufficient ground truth;
- `reference_status`: supported, ambiguous, suspect, or contradicted;
- contradiction observations with grader confidence;
- candidate epistemic state (`ASSERTED` or `ABSTAINED`) for each claim;
- candidate bounded confidence (`HIGH`, `MEDIUM`, or `LOW`) for each claim;
- evidence relevance, support, and authority;
- normalized relationship assertions such as `owns`, `observes`, `depends_on`, `must_precede`, and `must_not_own`;
- grader identity/configuration and per-observation confidence.

The template contains **no authoritative composite score**.

Misconception coverage is also a release property of the question bank, not just grader decoration. Every ownership-blueprint question must contain an explicit wrong-owner/authority contradiction; every critical question must contain an explicit contradiction with at least one `critical`-severity path; and every canonical misconception class must be exercised by at least one concrete SECRET contradiction. The `repository-misconception-coverage.v6` audit fails bank release if a taxonomy class has zero examples or if a misconception identifier encodes severity. This prevents labels such as observer/authority confusion, desired-vs-observed conflation, test-as-runtime-authority, or reversed dependency direction from existing only on paper. Misconception identity describes the kind of reasoning error; contradiction `severity` independently describes its risk. A label such as `critical_*` is therefore invalid taxonomy design. The audit also reports broad (`caller_owner_confusion`, `semantic_invariant_violation`) versus specific contradiction counts as a precision diagnostic only: broad classes remain valid where no narrower class dominates, and no ratio or balancing quota affects release readiness. Semantic-invariant contradictions are reclassified only when one existing specific misconception class clearly dominates the already-frozen SECRET contradiction semantics; mixed/cross-cutting invariants stay broad.

## 4. Evidence grading

Evidence correctness is not `citation_present = true`.

Oh-Goon mechanically checks candidate evidence-reference existence against the frozen repository evidence index. A nonexistent path/range or SECRET-tree reference is fabricated evidence and fails qualification.

The semantic grader separately judges:

```text
relevance
support
authority
```

This keeps mechanical existence validation separate from semantic appropriateness. A README may exist and mention a topic while still being weak evidence for runtime authority compared with runtime implementation plus invariant tests.

## 5. Score and qualify

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam score \
  /tmp/junior-agent-grades.json \
  --submission /tmp/junior-agent-answers.md \
  --output /tmp/junior-agent-result.json
```

Oh-Goon, not the grader, applies the versioned scoring and qualification policies.

Claim primitives currently begin with:

```text
SUPPORTED                +1.00
PARTIAL                  +0.50
MISSING                   0.00
INSUFFICIENT_EVIDENCE     0.00
UNSUPPORTED              -0.25
CONTRADICTED             -1.00
```

These values are policy data, not permanent architecture.

The result separates architectural dimensions from evidence/epistemic/mental-model dimensions. Understanding is combined with a weighted harmonic aggregate so a severe weakness cannot hide behind strong averages.

Qualification is evaluated against an explicit **authority/risk profile**, not a model-brand profile. The shipped profiles are:

```text
read-only.v1       understanding required before observation/review-only work
change-author.v1   default; understanding required before modifying repository code
release-author.v1  strongest; understanding required before release/promotion authority
```

This keeps model comparisons fair: different models evaluated for the same authority use the same thresholds. A model name, candidate label, or self-described mode cannot weaken the profile.

Qualification requires all applicable profile gates, including:

```text
overall minimum
critical-dimension minimums
sufficient evidence quality
sufficient grader confidence
sufficient valid/covered exam material
sufficient mental-model assertion coverage
no critical claim failure
no critical contradiction
no fabricated evidence
no critical cross-answer contradiction
no critical candidate overconfidence for write-authority profiles
```

Qualification levels are:

```text
UNCERTIFIED
LIMITED
NEEDS_ADJUDICATION
QUALIFIED
HIGH_CONFIDENCE
```

Grader confidence is not allowed to become hidden grading authority. A low-confidence semantic observation that would otherwise trigger a fatal claim/evidence/contradiction veto produces `NEEDS_ADJUDICATION` instead of automatically failing the candidate. Deterministic failures such as fabricated evidence and mechanically proven critical cross-answer ownership conflicts remain hard failures. The selected profile owns the critical-judgment confidence floor.

Candidate confidence is a different signal. The grader normalizes the candidate's own claim posture to `HIGH`, `MEDIUM`, or `LOW`, plus `ASSERTED` or `ABSTAINED`. Oh-Goon reports Brier-style calibration telemetry, high/low-confidence errors, accepted abstentions, and critical overconfidence. Abstention never creates semantic credit: an explicit `INSUFFICIENT_EVIDENCE` remains a zero semantic claim score, but it is distinguished from unsupported invention. `change-author.v1` and `release-author.v1` permit zero high-confidence errors on critical claims; `read-only.v1` reports the metric without turning it into write-authority policy.

A high numeric score does not override a critical ownership/lifecycle contradiction or critical overconfidence.

## 6. Repeated-run stability

One qualifying run is not stable write-readiness evidence. Repeated-run qualification uses the same exact repository, exam, scoring policy, authority profile, and frozen candidate configuration, but permits fresh session/context, question ordering, and random seed. The frozen candidate configuration is supplied as a JSON file to `grade-template`; its exact byte identity is carried into each scored result. Each run must also carry a distinct harness-issued `candidate_run_identity`, and the stability authority recomputes every result certificate identity before accepting it.

Combine three scored runs:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam stability \
  /tmp/run-1-result.json \
  /tmp/run-2-result.json \
  /tmp/run-3-result.json \
  --output /tmp/repository-understanding-stability.json
```

The current protocol requires exactly `3/3` individually qualified runs with distinct run identities and distinct, internally verified result certificate identities. It reports median composite score, worst critical dimension, claim agreement, concept agreement, and qualification rate. Claim/concept agreement percentages are diagnostic only: no empirical threshold has been invented. However, oscillation on a critical question/claim concept fails stability even when all three global runs individually qualify.

## 7. Cross-answer mental-model consistency

Grading does not stop at independent questions. Structured assertions are compared across the full exam.

The deterministic consistency pass currently detects conservative contradictions such as:

- the same relationship being both affirmed and denied;
- more than one asserted owner for a single-owner object;
- direct two-node `must_precede` cycles.

Critical ownership conflicts veto qualification. The result exposes `cross_answer_findings` and `mental_model_consistency` separately from per-question correctness.

This is the first step toward a fuller candidate-vs-expected repository relationship graph; the implementation does not pretend that full relationship-graph coverage exists yet.

## 8. Ambiguous questions and reference challenges

A candidate must not lose points because an exam question or SECRET reference is bad.

Questions can be excluded as:

```text
QUESTION_AMBIGUOUS
QUESTION_INVALID
INSUFFICIENT_GROUND_TRUTH
```

The grader can also mark the reference:

```text
REFERENCE_AMBIGUOUS
REFERENCE_SUSPECT
REFERENCE_CONTRADICTED
```

Excluded questions leave the effective denominator and remain visible for authoring review. Repository evidence remains the ultimate source of truth; SECRET is grader metadata, not product authority.

## 9. Exam coverage

The current milestone exposes an explicit `exam_coverage` object using `authored-question-blueprint.v1`. It reports effective question validity plus coverage of authored question types, difficulty levels, and content dimensions. Relationship-graph evidence is scored separately with concept-level sample coverage, precision/recall, missing critical edges, and forbidden-edge vetoes.

## 10. Offline regrading

A scored result contains a canonical `regrade_bundle` with the frozen exam snapshot, candidate submission, grader observations, deterministic evidence validation, and grader identity.

Recompute policy output without rerunning the candidate:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam regrade \
  /tmp/junior-agent-result.json \
  --output /tmp/junior-agent-regraded.json
```

The stable certificate identity excludes the act of regrading itself, so equivalent re-evaluation under the same frozen inputs/policies/profile preserves certificate identity. Changing the authority profile changes the qualification contract and therefore the certificate identity.

## 11. Human-readable report

The canonical result remains machine-readable JSON. Render its concise human summary with:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam report \
  /tmp/junior-agent-result.json
```

The report answers: authority profile, qualification, dimension strengths/weaknesses, exam coverage, grader confidence, adjudication requirements, critical failures, misconceptions, unsupported assertions, and fabricated evidence.

## Junior-agent safety boundary

This QA milestone makes a junior agent safer by producing stronger, evidence-backed qualification evidence before implementation work. It deliberately does **not** grant or enforce mutation permissions.

The desired later admission chain is:

```text
repository identity
  + exact exam identity
  + candidate identity
  + QA policy identity
  + qualifying certificate
        ↓
execution/admission policy decides whether implementation capability may be granted
```

Actual read-only/write authority remains the responsibility of execution/sandbox authority. Keeping those layers separate prevents QA from becoming a second security system.

## Exam integrity

- Candidate answers referencing `docs/certification/answers/*` fail closed.
- PUBLIC metadata must not leak exact SECRET answer/evidence paths or line references.
- SECRET uses semantic claims, not exact-string reference answers.
- Candidate source access should be read-only by capability when used as an admission exam.
- Evidence references in authored SECRET are validated mechanically by `verify`.
- Candidate evidence existence is validated from the frozen repository evidence index.
- Semantic evidence support/authority remains a grader observation, with policy owning the consequence.
- Scoring/rubric/schema/qualification/profile identities are explicit and persisted.
- Qualification profiles are selected by authority/risk class, never by model brand or candidate self-description.
- Low-confidence fatal semantic judgments require adjudication; deterministic fabricated-evidence and critical consistency failures remain fail-closed.
- Historical attempts can be regraded without rerunning the candidate.

## Adversarial challenges and benchmark integrity

Repository Understanding adversarial evaluation reuses the canonical SECRET question truth instead of creating a second answer authority. Hidden paraphrases, contradiction-pressure prompts, canaries, and fresh SHADOW challenges bind to an existing canonical question ID; the canonical question continues to own expected claims, evidence, contradictions, concept, dimension, and criticality.

Generate an identity-bound challenge set from the SECRET registry:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam challenge-set \
  --seed qualification-run-1 \
  --output /tmp/repository-understanding-challenges.json
```

Only the artifact's `public_delivery` is candidate-visible. `secret_bindings`, the rotation seed, and the registry remain grader-only. The assembler selects exactly one withheld paraphrase per equivalence family, includes every contradiction-pressure probe, and then deterministically rotates a bounded subset of the never-calibrated canary pool and fresh non-score-bearing SHADOW pool. The shipped policy currently selects one of three canaries and one of two fresh-SHADOW probes per challenge set. Candidate responses are graded against the same canonical semantic authority as the base question.

Adversarial observations are aggregated separately:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam adversarial \
  /tmp/repository-understanding-challenges.json \
  /tmp/adversarial-observations.json \
  --output /tmp/adversarial-report.json
```

Paraphrase agreement, evidence-backed contradiction-pressure resistance, canary consistency, fresh-SHADOW consistency, and false-premise rejection are reported as diagnostics; this phase does not invent empirical percentage thresholds. A failed hidden challenge bound to a critical canonical question is a hard adversarial veto. False-premise coverage is required across every canonical `FALSE_PREMISE` question so a candidate cannot qualify by silently accepting an embedded architectural error.

Every authored SECRET evidence reference is also frozen into the exam snapshot by exact current source-file SHA-256 and exact referenced-range SHA-256. This makes later offline grading/regrading detect byte drift rather than assuming that an unchanged path or line number still names the same evidence.

Benchmark comparability is a separate harness/control-plane authority. Before comparing candidate runs, record execution evidence and verify it with:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam integrity \
  /tmp/benchmark-execution-evidence.json \
  --challenge-set /tmp/repository-understanding-challenges.json \
  --output /tmp/benchmark-integrity-report.json
```

Comparable runs require control-plane evidence that network access, prior conversation history, prior candidate answers, grader SECRET material, and candidate-workspace mutation were denied, while a fresh session and frozen candidate configuration were required. Missing or contradictory control evidence yields `NOT_COMPARABLE`. Candidate self-attestation is never treated as enforcement authority.

Canary and fresh-challenge `introduced_source_version` is historical identity. It is immutable after introduction and may be older than the current checkpoint; validators reject future introduction versions rather than rewriting existing challenge history on each release.

## Human baseline and question lifecycle

Human baselines are empirical evidence, not grading authority. Record independent expert observations with the exact repository/exam identity, per-question outcome, time-to-evidence, and reference assessment (`AGREE`, `SUSPECT`, or `AMBIGUOUS`):

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam human-baseline \
  /tmp/human-baseline-observations.json \
  --output /tmp/human-baseline-report.json
```

The report exposes median expert time, success rate, reference-assessment distribution, and cross-reviewer disagreement. No timing, success, agreement, or disagreement value automatically changes difficulty, scoring, qualification, or question state.

Question bank governance uses the SECRET lifecycle:

```text
DRAFT -> SHADOW -> CALIBRATING -> ACTIVE -> RETIRED
```

Only `ACTIVE` questions are score-bearing. The existing 147-question bank is explicitly grandfathered `ACTIVE`; its introduction predates lifecycle tracking. New experimental material stays on the non-score-bearing hidden challenge/lifecycle surface until promotion evidence exists.

A transition is an explicit bank-authority action, not an inference from telemetry. `CALIBRATING -> ACTIVE` requires an identity-bound calibration report, a human baseline with at least two independent reviewers, and an explicit promotion-review identity. If human reviewers disagree with the reference, an adjudication identity is additionally required. Numeric performance thresholds are deliberately not invented. `ACTIVE -> RETIRED` similarly requires an explicit retirement review and reason.

Validate one transition request without mutating the source bank:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam lifecycle-transition \
  /tmp/question-transition-request.json \
  --calibration-report /tmp/calibration-report.json \
  --human-baseline-report /tmp/human-baseline-report.json \
  --output /tmp/question-transition-authorization.json
```

The resulting authorization is evidence for the SECRET bank owner. It cannot mutate source material, candidate scores, qualification, or release state by itself.

## Capability-scoped Repository Understanding certificates

Repository Understanding capability certificates are derived evidence, not a candidate-selected permission. A single passing score cannot issue one. The issuer recomputes the existing three-run stability decision from the original scored result artifacts and requires one independently verified benchmark-integrity report plus one adversarial report for each exact run identity.

Issue a certificate only from the complete evidence set:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam capability-certificate \
  /tmp/result-1.json /tmp/result-2.json /tmp/result-3.json \
  --integrity /tmp/integrity-1.json /tmp/integrity-2.json /tmp/integrity-3.json \
  --adversarial /tmp/adversarial-1.json /tmp/adversarial-2.json /tmp/adversarial-3.json \
  --output /tmp/repository-understanding-capability.json
```

The capability tier is derived from the already-selected authority/risk qualification profile; the candidate cannot request a stronger tier:

```text
read-only.v1     -> PLAN
change-author.v1 -> STANDARD
release-author.v1 -> CRITICAL
```

The current action projection is deliberately small and policy-owned. `PLAN` permits planning and code review only. `STANDARD` additionally permits standard editing. `CRITICAL` additionally carries critical-execution and release-promotion evidence. No separate low-risk-edit tier is invented because no calibrated qualification profile currently owns that distinction.

Issuance requires all of the following:

- exact 3/3 stable qualification recomputed from the three original result artifacts;
- no critical-concept oscillation across those runs;
- the same frozen candidate-configuration identity across all runs;
- one `COMPARABLE` benchmark-integrity report for every exact candidate run identity;
- a distinct hidden challenge instance for every qualifying run;
- one adversarial report for every exact challenge instance, with no critical adversarial veto;
- one exact repository identity, exam snapshot, grading/scoring/qualification policy set, and capability policy identity.

The certificate carries exact repository and candidate-configuration freshness bindings. Version 1 deliberately does **not** invent an arbitrary wall-clock TTL; concept-bound source invalidation belongs to differential recertification, and any future expiry rule requires its own calibrated policy.

A deterministic action predicate can validate one requested action:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam capability-check \
  /tmp/repository-understanding-capability.json \
  --repository-identity sha256:... \
  --candidate-configuration-identity sha256:... \
  --requested-capability standard_editing \
  --output /tmp/repository-understanding-capability-check.json
```

Both artifacts are explicitly `EVIDENCE_ONLY`. Their canonical SHA-256 identities detect accidental/tampered content relative to the supplied artifact, but they are not issuer signatures and do not become a second security system. The execution/admission owner must independently trust the certificate source, verify the exact repository/candidate/action binding, and remain the sole authority that can grant mutation or release capability. Without a trusted valid certificate the baseline remains `READ_ONLY`.

## Differential recertification

A capability certificate is exact-source bound. Any repository identity change suspends that certificate first. `recertification-plan` then compares one qualifying prior result's embedded frozen exam snapshot with the current exam snapshot and reports which declared concept bindings changed.

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam recertification-plan \
  /tmp/repository-understanding-capability.json \
  /tmp/repository-understanding-result-run-1.json \
  --output /tmp/repository-understanding-recertification-plan.json
```

Concept binding is deliberately narrower than whole-repository byte identity: it includes question semantics/lifecycle and each SECRET evidence reference plus its exact range SHA-256. A file changing outside a cited range therefore does not automatically invalidate that concept, while changed cited bytes, claims, contradictions, question semantics, lifecycle, added concepts, or removed concepts do. The plan emits the minimal current ACTIVE question IDs for affected/new concepts.

The planner is `EVIDENCE_ONLY`. Preserved concepts may reduce the next exam scope, but the old certificate remains stale for new source bytes until a trusted recertification/reissue owner produces new governed capability evidence. Unmapped source changes are reported as diagnostics and do not silently become proof of no impact.

## QA Game Tape / certificate explainability

The QA Game Tape is a read-only investigation projection over existing Repository Understanding authorities. It does **not** score answers, promote questions, issue capability, extend certificate freshness, or grant runtime permission.

Build one Game Tape from the same three scored runs and exact benchmark/adversarial evidence used by the capability certificate:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam game-tape \
  /tmp/run-1-result.json /tmp/run-2-result.json /tmp/run-3-result.json \
  --integrity /tmp/integrity-1.json /tmp/integrity-2.json /tmp/integrity-3.json \
  --adversarial /tmp/adversarial-1.json /tmp/adversarial-2.json /tmp/adversarial-3.json \
  --certificate /tmp/repository-understanding-capability.json \
  --recertification /tmp/repository-understanding-recertification-plan.json \
  --output /tmp/repository-understanding-game-tape.json
```

The canonical views are `overview`, `concepts`, `questions`, `evidence`, `consistency_epistemics`, `stability`, `benchmark_integrity`, `qualification_capability`, and `recertification`. Overview includes the existing Oh-Goon "what remains" framing so investigators can immediately see critical failed questions, capability gates, and any concept/question recertification scope.

Before projection, Oh-Goon recomputes the three-run stability result and capability certificate from the supplied evidence and requires the exact capability identity. A certificate whose self-hash is internally valid but whose content cannot be reproduced from its evidence is rejected. The Game Tape carries its own integrity identity, but that identity is not an issuer signature and never replaces the existing runtime/admission owner.

Render a concise terminal summary with:

```bash
uv run --locked --project backend oh-goon-repository-understanding-exam game-tape-report \
  /tmp/repository-understanding-game-tape.json
```

SECRET expected claims, contradiction prose, and grader-only answer text are deliberately not copied into the projection. Candidate evidence references and already-scored outcomes may be shown because they are investigation evidence rather than a second answer bank.
