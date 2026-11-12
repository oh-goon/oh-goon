# OH-Goon evidence-driven orchestration requirements

**Status:** Proposed delivery requirements; implementation and qualification are not implied.  
**Scope:** Strengthen admission, execution custody, native supervision, exact accounting and recovery through the existing lifecycle.  
**Prepared:** 2026-11-10.  
**Paired delivery document:** `Hugloss/Hashmarks:docs/plans/HASHMARKS_EVIDENCE_QUALITY_REQUIREMENTS.md`.

## 1. Goal and governing owners

> **Make declared work more reliably admitted, executed, accounted for and recovered using qualified Hashmarks evidence and native tools. Do not make OH-Goon another repository analyzer or execution engine beside its existing lifecycle.**

The [mandatory Hashmarks SSOT requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md) own repository semantics and `oh_goon.integrations.hashmarks`. The [canonical execution-lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md) own Run custody and terminalization. The [current execution handoff](HASHMARKS_EXECUTION_LAYER.md), [execution supervisor](EXECUTION_SUPERVISOR.md) and [native-tool authority](NATIVE_TOOL_AUTHORITY.md) locate the existing owners.

This is a bounded improvement/acceptance plan under those contracts, not a replacement state machine, alternate requirement authority or runtime registry. Resolve conflicts at the canonical owner. Imported historical baselines and target worker descriptions do not establish current implementation status; inspect current source and applicable tests before proposing changes.

Hashmarks remains entirely focused on qualified repository evidence. OH-Goon owns whether that evidence satisfies its policy, the exact work it admits, execution custody, supervision, outcomes and recovery. Coding agents retain reasoning and edits. Native Git, uv, npm, test runners and other selected tools retain their native semantics.

### Explicit exclusions

This initiative introduces **no Pierre integration or presentation features** in either repository: no GUI, viewer, dashboard, HTML report, renderer, readability product, narrative generator, frontend package or UI-oriented API. Existing application behavior is not deleted. References below to Game Tape mean preservation of existing execution facts and lineage, not a new view or display feature.

Also excluded: another CodeMap, repository selector, Git/package resolver, task graph, scheduler, broker, worker route, Run model, result store or compatibility fallback. Improve existing product responsibilities without duplicating native facilities.

## 2. Required product outcomes

| ID | Outcome | Acceptance obligation |
| --- | --- | --- |
| OE-01 | Producer-qualified admission | Bind supported Hashmarks facts to the required loaded producer, selected source and policy without rederiving repository meaning. |
| OE-02 | Immutable execution custody | Preserve what was admitted, transported/materialized and actually executed; a newer query cannot rewrite an admitted Run. |
| OE-03 | Native execution authority | Invoke the selected native environment through the existing supervised path; preserve native configuration, outcomes and failure meaning. |
| OE-04 | Exact work accounting | Account for every admitted logical member and attempt without omissions, unapproved additions or false success. |
| OE-05 | Safe interruption and recovery | Deterministically handle pre-effect refusal, partial effects, cancellation, worker loss and uncertain cleanup; no blind replay. |
| OE-06 | Contract-safe producer evolution | A supported refinement of Hashmarks evidence must not silently erase an independent execution, privacy or verification obligation. |
| OE-07 | Better resource and feedback economics | Reduce meaningful feedback latency or repeated work under the selected resource profile without weaker obligations or unsafe reuse. |
| OE-08 | Scope and disclosure integrity | Keep Run, source, credential/project scope and recovery authority separate; retain only evidence allowed by the existing disclosure/retention contracts. |

The same requirements apply where relevant to local, CI, scheduled, agentic, application and ML work using the existing lifecycle. The first verification scenario is not a redesign of OH-Goon as a test-only product. Not every declared workflow needs a new Hashmarks nomination: ordinary native batches retain their existing authoritative input path.

## 3. Admission and evidence preservation

### 3.1 One producer and one composition boundary

Use only the public `oh_goon.integrations.hashmarks` exports for Hashmarks-backed intelligence. Producer-native validation owns its own schemas, identities and qualification. OH-Goon binds the validated result to its independent admission obligations; it must not recompute producer identities or reopen private graph/packet internals to reconstruct meaning.

Required Hashmarks startup identity and mandatory operation facts must be established under the existing contracts before their dependent stateful/effectful work. Missing or incompatible mandatory producer authority cannot trigger another analyzer, bypass or optional provider mode.

Incomplete optional explanatory evidence is not automatically a global outage. Follow the owning contract for that fact and operation. Broader verification or another conservative policy is allowed only where existing policy permits it and all mandatory source, identity and authorization facts are established. Keep the original uncertainty explicit; conservative execution does not manufacture repository truth.

### 3.2 Preserve distinctions that affect decisions

Do not collapse unresolved, stale, invalid, foreign, ambiguous, denied or truncated evidence into false, empty, complete or successful results. Preserve producer reasons, scope and material omissions through the existing validated boundary.

A verifier relationship is not a coverage guarantee. A qualified membership is not permission to execute. A previous pass is not a pass for new inputs. A native Git status witness is not complete repository-content identity.

Repository-content observation identity and execution-custody identity may describe the same bytes while proving different facts. Preserve both where their scopes require them. Do not delete Git witnesses, allowed-write checks, materialization receipts or custody hashes simply because Hashmarks also observes source.

### 3.3 Admit once; do not silently reselect

Bind the selected source and producer evidence to the existing immutable admission. Material source or policy drift before an effect follows the existing invalidation/refusal/readmission path. Do not silently refresh Hashmarks, replace selected members or resolve ambient configuration inside a worker to make an old admission look current.

Refining the selection algorithm may intentionally change future selected work after new admission. It may not mutate membership of a Run already admitted. Better evidence must still be allowed to improve future decisions without freezing accidental historical result shapes.

## 4. Execution, outcomes and recovery

### 4.1 Use the existing lifecycle and native tools

Local, CI, scheduled and agent work must enter the same admission, Run/Step/Attempt supervision and terminalization owners. No new path is authorized. Consume the already selected native environment; do not resolve package-local tools again or require duplicate model/provider/executable declarations without an actual new capability or trust boundary.

OH-Goon may own cwd, authorized environment/credentials, resource ceilings, deadlines, cancellation and evidence capture. It must not reproduce native installation, package cache, dependency resolution, inner retry or Git lifecycle semantics. Outer retry policy remains OH-Goon-owned and must account for the native operation's actual outcome and replay safety.

### 4.2 Exact logical accounting, with attempts separate

For an admitted verification membership, the terminal record must account for every member exactly once in the existing authoritative logical outcome partition. Retain attempt history separately: legitimate retries can produce multiple attempts, but they cannot produce duplicated logical completion or hidden repeated effects.

Grouping or timeout-driven refinement must conserve admitted membership. Native collection/execution results must be reconciled with admitted obligations; missing, extra, duplicate or non-passing required members cannot become successful completion. Never-started work retains its blocking cause using the existing outcome vocabulary.

Results retain the applicable admission, source/custody, producer provenance, environment/mode, Run/Step/Attempt and native outcome bindings. Bind only dimensions material to reuse. Raw logs or repository packets must not become a new mandatory archive: the current handoff deliberately retains some raw producer evidence only transiently. Existing digests and permitted references remain subject to their disclosed retention limits.

### 4.3 Failure-cut obligations

Implement through the existing lifecycle owner and existing state vocabulary. The table specifies required semantics; it does not introduce enum names or a new failure manager.

| Failure cut | Required behavior and evidence |
| --- | --- |
| Refusal before any effect | No work begins; retain the owning refusal and its relevant evidence. |
| Source/policy drift between admission and first effect | Refuse or invalidate under the canonical admission contract; do not replace frozen evidence in place. |
| Partial resource binding or materialization | The existing custody owner accounts for created resources, teardown and cleanup failure; later work does not proceed as if binding completed. |
| Native failure after effects begin | Preserve the native outcome and known effects; do not substitute another command as an equivalent proof. |
| Cancellation racing completion | The existing authoritative event/transaction ordering determines the outcome; timestamps or the last arriving callback do not invent a winner. |
| Worker/controller loss before terminal evidence | Retain uncertainty and existing containment/reconciliation handling; neither silence nor a matching fingerprint proves no effect occurred. |
| Teardown cannot be proved complete | Do not claim quiescence or safe replay; preserve the base outcome and apply the canonical containment/recovery rule. |
| Retry, resume or recovery | Reuse only qualified permitted evidence, obtain the required current authority, and retain new attempt/recovery lineage without rewriting original history. |

No exactly-once external effect is promised solely because a Run exists. Do not introduce distributed leases, a journal or a custom supervisor when the established owner already supplies the required guarantee. A missing guarantee must be narrowed or implemented at that owner, not hidden by a fallback.

## 5. Deterministic headless acceptance scenarios

Map these to current tests before adding any. Use controlled barriers, injected failures or native fixture receipts rather than timing luck. Fakes at unit seams do not replace integration checks with the selected real producer and native tool.

| ID | Scenario | Required proof |
| --- | --- | --- |
| OC-01 | Mandatory Hashmarks identity is unavailable/incompatible; separately, an optional explanatory observation is incomplete. | Mandatory work refuses before dependent effects; optional uncertainty follows its own contract without a local intelligence substitute. |
| OC-02 | Replace one admitted source member after evidence qualification while HEAD/dirty state remains the same. | Custody/admission does not execute the substituted source as if it matched; historical admitted evidence is unchanged. |
| OC-03 | Supply supported refined producer evidence for the same fact, plus genuinely unknown and ambiguous controls. | Independent consumer obligations survive; unsupported cases are not promoted to proven facts. |
| OC-04 | Inject missing, extra, duplicate and reordered collected membership. | Exact admitted obligations are preserved under the actual ordering contract; no empty-green or false completion. |
| OC-05 | Regroup admitted work; cancel after one completed attempt; then consider authorized resume. | Logical membership remains conserved, unstarted work is accounted for, and only currently qualified permitted results are reused. |
| OC-06 | Race cancellation with native completion through controlled barriers. | One canonical outcome order; no later callback overwrites stronger terminal/custody evidence. |
| OC-07 | Lose worker/controller after an external effect but before a terminal receipt; separately fail teardown. | Uncertain effects and containment remain explicit; no blind replay or fabricated no-effect claim. |
| OC-08 | Change source, producer contract, relevant environment, credential/project scope or policy before result reuse. | Materially foreign evidence is rejected; authorized cross-Run continuation follows the existing explicit handoff, not implicit trust. |
| OC-09 | Exercise native failure/retry behavior and applicable CLI/API/scheduled entrypoints. | The same supported lifecycle and selected native authority remain in use; no hidden resolver or alternative execution route. |
| OC-10 | Supply a failure diagnostic correlated by Hashmarks to changed source. | Source correlation and actual Run outcome retain distinct authorities; no inferred causation, repair choice or success claim. |
| OC-11 | Compare a bounded feedback/resource optimization before and after. | Comparable obligations, outcomes and source identities; lower measured cost without a new scheduler/cache authority or increased unsafe reuse. |
| OC-12 | Change denied/private evidence without changing admitted public inputs. | Public selection, output and persisted evidence obey the existing disclosure contract; no credentials or private grading material enter Hashmarks retrieval. |

## 6. First joint scenario and measurement

Use a single headless journey, through existing public interfaces:

```text
caller supplies an admitted source change and selected scope
  -> Hashmarks returns qualified repository evidence
  -> OH-Goon validates the supported producer result and applies its policy
  -> the existing lifecycle executes the admitted native work
  -> terminal evidence accounts for work and retains the source/producer bindings
```

Start from current implementation, not from an assumed gap. Freeze the workload, versions, relevant source/policy/producer identities and independent expected outcomes. Exercise success, incomplete evidence, pre-effect source drift and one interrupted-effect case. Link applicable HC scenarios from the paired Hashmarks handoff to applicable OC scenarios here; do not invent a shared runtime schema for the test.

Measure time to a trustworthy useful result/failure, repeated source/evidence work, native tool invocations, CPU/RAM and accounting correctness where applicable. Local and CI profiles are different experimental conditions; native resource configuration remains authoritative. Freeze success criteria before the candidate measurement. No arbitrary percentage or universal latency promise is introduced by this document.

A benefit is valid only when the correctness and privacy obligations remain satisfied. An optimization may choose different work for a newly admitted Run if policy allows and the relevant coverage obligations are independently established. It cannot silently narrow an admitted Run or replace release/full-suite obligations with a cheaper subset.

## 7. Delivery and ownership

| Work package | Responsible owner | Exit condition |
| --- | --- | --- |
| O0: qualify the current path | Existing integration and admission owners | Pin source/loaded producer; map one workflow and tests to SUPPORTED/PARTIAL/DEMONSTRATED GAP/UNKNOWN. Do not infer an implementation defect from an old requirement or open issue. |
| O1: preserve producer contracts | `oh_goon.integrations.hashmarks` plus the relevant Hashmarks evidence owner | Fix only demonstrated handoff loss or consume a justified producer enhancement; paired semantic-refinement and uncertainty tests pass. |
| O2: strengthen lifecycle proof | Existing admission, supervisor, Step/Attempt and recovery owners | Close only demonstrated custody/accounting/recovery gaps with deterministic failure-cut tests; no new runtime, scheduler or compatibility path. |
| O3: measure and close | Existing QA/qualification owners | Comparable improvement evidence, actual checks, remaining limits, and updates to affected owning contracts. Comparative agent scoring remains EvalShot-owned. |

A producer change ships first only when a consumer change actually depends on it; OH-Goon-only lifecycle improvements need not modify Hashmarks. Select producer releases through ordinary dependency intent and the native lock. No private wheel injection, copied release identity, second installation path or legacy semantic fallback.

Bundle related corrections into coherent ready-for-review PRs. Use existing issue tracking; no task registry, review service or new global CI gate. A bounded complete slice may ship without waiting for an unrelated repository-wide sweep.

## 8. Review, validation and recovery

Use existing [AgentsCookbook skills](https://github.com/Hugloss/agentsCookbook/tree/4e083f49daf3bfe4e53d7528a82dea04d93914ef/skills): `requirements-lifecycle-closure-review` for effects and recovery; `canonical-consumer-contract-review` for supported producer evolution; `evidence-provenance-binding-review` for unsafe substitution/reuse; `native-tool-authority-review` for delegation; `fact-grounding-auditor` and `plan-contract-guard` for the final handoff. Use the narrowest applicable skill and report one cause once. Missing evidence remains unresolved rather than a clean bill of health.

Follow the current [contribution and verification workflow](../CONTRIBUTING.md) and [QA baseline](REAL_LIFECYCLE_QA.md). Run focused proof while editing and the repository-required checks for the actual change. Keep unrun native/release/environment qualification explicit. Do not change required checks, use mocks as native qualification, or introduce a mandatory benchmark for every documentation/maintenance PR.

For code rollback, restore a supported source/dependency combination through the existing release/dependency owners. External effects cannot be undone by reverting code: reconcile or quarantine uncertain work under the current recovery contract. Do not relabel old evidence, downgrade its interpretation, re-enable a legacy intelligence path or replay an uncertain operation to make rollback appear successful. Documentation-only changes have no runtime rollback procedure.

## 9. Completion and evidence boundary

Completion requires a demonstrated useful orchestration improvement or protected guarantee; intact producer/native authority; complete accounting for the selected failure paths; actual applicable checks; and no presentation, intelligence-replacement or parallel-runtime work. Clearer documentation alone is not a measured runtime improvement.

Planning references were inspected at OH-Goon `288d93806f96d196c87b6aa1e30c4a8674b64bb5`, Hashmarks `dfcebc228e7edec0f55185ec5da333969d36012a`, and AgentsCookbook `4e083f49daf3bfe4e53d7528a82dea04d93914ef`. Referenced requirements, owner maps and handoff documentation establish the intended boundaries, not complete current compliance. Refresh applicable implementation/test evidence before acting. No product tests, benchmark results or complete lifecycle qualification are claimed here.
