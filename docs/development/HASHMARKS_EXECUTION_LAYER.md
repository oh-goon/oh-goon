# Hashmarks repository-evidence → Oh-Goon execution handoff

The frozen product boundary is:

- **Hashmarks = repository-evidence plane.**
- **Coding agent = solution plane.**
- **Oh-Goon = execution and certification control plane.**

Hashmarks describes what repository evidence says matters. The coding agent decides what change to make. Oh-Goon decides whether, where, and how admitted work may run and whether the resulting execution evidence is certifiable.

The [mandatory Hashmarks requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md)
own repository semantics and the public integration boundary. The separate
[execution-lifecycle requirements](OH_GOON_CANONICAL_EXECUTION_LIFECYCLE_REQUIREMENTS.md)
own Run custody. This guide describes the current handoff and its remaining
migration limits; requirements alone do not establish qualification.

## Ownership

Hashmarks owns repository identity/freshness, CodeMap relationships, static cross-project topology, dependency-consumer meaning, impact/provenance, flat verification selection membership, repository-negative evidence, repository/source correlation and repository-evidence provenance. Physical grouping/process-isolation hints are not admitted as execution authority.

Oh-Goon owns process launch and process-tree cleanup, deadlines, bounded concurrency, retry/resume admission, environment recovery, Fleet scheduling, execution/refinement grouping, result authority, execution provenance, certification provenance, Repository Understanding qualification, certificates, and Game Tape.

Repository/content observation identity describes what Hashmarks observed in its
source scope and generation. Oh-Goon's execution-custody/materialization identity
describes the exact bytes frozen, transported and exposed to an admitted attempt.
These identities may concern the same bytes while proving different facts;
neither substitutes for the other's authority. A workspace path or matching hash
does not itself grant effect permission or live execution custody.

A thin Oh-Goon adapter consumes required Hashmarks evidence. Runtime startup verifies the loaded producer before database creation, recovery or execution. Missing or invalid producer authority fails startup; there is no local repository-intelligence fallback. The adapter must never call Hashmarks execution-loop helpers such as `execute_shard`, `run_next`, or `run_warmup`, and it must never build a second CodeMap/impact/verification-selection engine.

## Current admitted contract

Hashmarks is selected as an ordinary released Python dependency by `backend/pyproject.toml`; native `uv.lock` owns the exact selected artifact and `backend/.venv` owns materialization. Oh-Goon does not vendor, reacquire, repackage, or privately inject a Hashmarks release.

`oh_goon.integrations.hashmarks` is the single public Python integration API.
Its `__all__` derives from one explicit capability export table. Production
consumers import those names from the package root; private integration modules
are internal implementation owners. Exports resolve lazily to the original
callable or class, preserving its implementation identity and source origin.
Importing the API or selecting a capability does not load the producer or observe
a repository. Architecture checks reject direct producer imports, private
integration imports, undeclared exports and qualified module-attribute bypasses.

Hashmarks evidence is validated through supported producer/consumer contracts and bound to the producer implementation identity reported by the actually loaded Hashmarks runtime. The source tree does not carry a copied "current Hashmarks version/commit/tree/wheel" authority.

The producer supplies exact flat verification membership and repository/evidence provenance; `execution_layout=external` keeps physical grouping, workers, isolation and recovery exclusively Oh-Goon-owned. Artifact identity remains caller/admission provenance when supplied, while producer compatibility is determined by the loaded implementation identity.

The retired command-bearing verification adapter is not part of this API.
Hashmarks verification selection enters through its native flat membership
contract; producer `argv`, working-directory and execution-policy fields cannot
grant execution authority. Oh-Goon's producer-neutral described-work model and
assignment authority retain their own checkpoint, source-generation and custody
checks independently of Hashmarks.

Hashmarks standalone CLI/MCP installation is a separate external host concern owned by Hashmarks and the host application. The Oh-Goon backend consumes the Python API through native uv materialization and never launches the standalone CLI as a second dependency path.

Producer-native validation owns Hashmarks schemas, member ordering, duplicate
membership rules, and producer/member/envelope/contract identity generation.
After that validation, Oh-Goon checks its own admission bindings: the loaded
implementation, admitted repository and source, freshness, repository-evidence
scope, and external execution ownership. Safe structured pytest projection and
duplicate executable-target rejection belong to Oh-Goon execution; they do not
justify copying Hashmarks validation or recomputing producer identities.

The public validator's detached, typed selection projection is the consumption
boundary. It includes opaque contract/evidence identities as well as membership
and provenance. The adapter resolves the loaded implementation once per admission
and consumes that projection; it does not reopen producer fields after validation.

The admitted `repository_provenance_identity` is carried unchanged into
execution-plan references and the terminal receipt. Regrouping may change an
Oh-Goon work identity, but it cannot change admitted producer provenance or
membership. Terminal accounting must partition that membership exactly once,
including failure, cancellation, timeout, and unexecuted remainder.

Historical interoperability series remain audit evidence for their original
sources. They neither select the current dependency nor create a compatibility
fallback. Offline terminal-evidence verification inspects a retained result;
it cannot promote that result into admission for new work.

## No execution-policy smuggling

The `hashmarks.verification-selection-envelope.v2` envelope and
`hashmarks.downstream-verification-contract.v2` contract carry repository evidence
only. Oh-Goon admits the exact contract shape and rejects extra policy fields.
The envelope does not control:

- argv/command construction;
- timeout/deadline;
- retry count or delay;
- workers/concurrency;
- schedule/machine placement;
- process lifecycle;
- environment recovery;
- result authority.

Those remain Oh-Goon-owned inputs.

## Execution grouping and isolation

Hashmarks owns selected membership, not physical shards. Oh-Goon applies its own explicit grouping policy and process/isolation authority after admission. Producer fields such as `preferred_granularity`, `may_regroup`, `isolated_process`, worker hints, or physical shard layouts cannot gain authority through the Hashmarks contract.

Local, CI, scheduled, agentic and ML work use the existing FleetRun admission and
`FleetRunExecution` → `ExecutionSession` → `FleetTargetScheduler` → Step/Attempt
path. The [supervisor contract](EXECUTION_SUPERVISOR.md) owns its fresh Run
process and atomic ExecutionTicket custody claim; HUG-181/HUG-183 retain broader
qualification. This strengthens the same execution path without a Hashmarks executor. The claimed
worker must consume frozen admitted facts without re-resolving or reselecting
logical work; source drift requires the original invalidation/admission handling.

Oh-Goon may refine execution grouping after a timeout only if the exact admitted membership is conserved:

```text
union(children) == parent membership
no omitted node
no duplicate node
no added node
```

Refinement is owned by the current Oh-Goon execution/recovery path. Any refinement implementation must conserve the admitted parent membership exactly; there is no retained adapter for an older Hashmarks shard-layout contract.

## Resume evidence

Only PASS results are resumable. Evidence schema remains `oh-goon.shard-pass-evidence/v1`, but the evidence/key now binds the material repository-evidence inputs as well as execution work:

- admitted repository generation identity;
- Hashmarks inner plan identity;
- Hashmarks outer repository-evidence/envelope identity when present;
- Hashmarks repository content identity when present;
- Hashmarks selection-input identity when present;
- producer artifact and implementation identity when present;
- producer algorithm/version and source schema;
- shard ID and work identity;

Therefore identical node membership from a different envelope or producer artifact cannot reuse prior PASS evidence silently.

## CI scheduling

There is deliberately no `HashmarksSchedule`, background polling loop, or second scheduler. Scheduled execution remains an ordinary Oh-Goon Fleet Plan/Schedule occurrence.

## Consumer boundary

The live interoperability surface is the consumer-conformance contract supplied by the selected, loaded Hashmarks dependency. Oh-Goon admits the exact flat membership and then constructs its own `VerificationExecutionGroupingPolicy` and execution plan. Offline proof verification inspects existing terminal evidence only; it is not proof reuse and cannot admit an old proof as a new execution.

Hashmarks selects membership and repository evidence. Oh-Goon chooses whether, when, where, and under what execution/certification policy it runs.

## Local evidence consumers

Readiness obtains explanatory authored Python metadata through the public
`observe_python_project_declarations` capability. Its public readiness result
retains the exact project declaration, provider input revisions, repository
qualifiers, producer observation identity and loaded identity. The explicit
metadata disclosure excludes unrelated dependency/source configuration and
grants no absence claim over omitted declarations. Missing, invalid and unsupported input do
not become a complete empty project. Authored `requires-python` does not select
an interpreter. Native uv readiness, the local runtime pin, and the selected
extension's launch-entrypoint binding remain native/execution configuration.
Producer input failures appear as unavailable explanatory metadata alongside
native uv's original readiness result. A missing or incompatible mandatory
producer remains an admission failure.

Repository Understanding's evidence-reference hashes and exam input index bind
the exact bytes retained for grading and differential recertification. They are
execution/qualification custody facts, separate from Hashmarks repository
identity and freshness. The existing package owner supplies that frozen input
membership; SECRET answer files are excluded before source indexing. Grader
answer choice, line-coordinate validity, question bindings, exam identity and
grading stay with Repository Understanding and are never sent to Hashmarks as
repository truth. These custody hashes cannot prove repository relevance,
currentness, completeness or absence.

The data-only dependency Step uses the explicitly selected native ecosystem adapter and dependency qualification
contract. Its inventory and absence claims describe the supplied lock, not authored
requirements or an installed environment. See [dependency observations](DEPENDENCY_OBSERVATIONS.md).
Hashmarks qualifies the declared input scope, per-context inventory coverage and
negative evidence. Source correspondence, currentness of that input scope, and
repository-wide CodeMap freshness remain separate facts. Oh-Goon applies its own
admission policy to those facts rather than deriving producer qualification again.

Ordinary pytest batches collect through native pytest hooks and retain exact selected
node IDs. They do not need a Hashmarks relevance nomination. A Hashmarks-selected
subset still crosses the producer-validated flat membership boundary above.

Built-in Agent repository discovery/search uses named CodeMap owners over the original
captured bytes. Each local tool session owns one disposable materialization/index;
source observations retain native bounds and revisions. Index preparation runs inside
the original supervised tool process, under its timeout and cancellation budget.
The existing tool runs as `python -m oh_goon.integrations.hashmarks`; this public
package entrypoint invokes the same original tool implementation.
Every model-backed Agent request starts with Hashmarks task evidence over that
captured repository, including provider review, adjudication and proposal. The
producer owns relevance, ambiguity, verification nominations, source observations
and their presentation. Oh-Goon binds the capture and loaded implementation,
places that evidence before repository text in model context, and includes the
original packet in transient supervised Step output. Durable stream evidence
retains hashes, byte counts and line counts; typed review/adjudication claims
persist separately. Historical Game Tape does not retain raw producer packets
or reconstruct model/tool transcripts. See the
[Agent runtime evidence inventory](AGENT_RUNTIME_OWNERS.md#results-limits-and-observations).
Follow-up tool evidence uses the same
producer boundary and native presentation, retaining retrieval locators, source
observations and their producer qualifications. Local model context has an
explicit combined bound; oversized native disclosure fails before a model call
instead of cutting JSON or discarding qualifications. Exact captured file reads
retain their separate 2,048-byte delivery bound.
Evidence failure stops the Step before model disclosure;
there is no option to skip evidence or replace it with a local selector.
A bounded result cannot prove
repository-wide absence or replace the complete scope of a provider review. Exact
reads of captured repository and Skill resources remain byte delivery.

Filesystem identity uses Hashmarks' public local directory observation. It returns
native input-root identity and separately named physical materialization counts;
ordinary selected-input counts can omit entries that physical storage still owns.
Each requested child has its own native contents scope. Directory member revisions
use another hash domain and cannot substitute for child input-root identity.
Oh-Goon chooses exclusions, owns cancellation and scratch cleanup, and retains
source copying, path containment and execution-boundary re-verification.
The integration inherits Hashmarks' default bound of 100,000 physical entries;
exceeding it fails identity admission without returning partial evidence.

Make/shell declarations, Python manifests, byte-range receipts and ECMAScript
syntax are supported repository observations. Authored shell text does not
establish native expansion or effective environment, and bounded source syntax
does not establish complete alias closure or runtime call targets. Current
architecture/tooling still contains local Python, TypeScript and Make/shell
semantic analyzers. Their retirement is remaining
[HUG-179](https://linear.app/hugloss/issue/HUG-179) migration work, not permission
for another repository-intelligence owner or a fallback when producer evidence
is incomplete. Oh-Goon's architecture verdicts, approved-owner rules and policy
lists remain Oh-Goon-owned; repository facts must come through the public API
with their qualifications. Public import protection does not prove that this
semantic migration is complete. Qualification grading and SECRET answer truth
remain outside repository retrieval.

Detailed producer implementation provenance is unavailable until a supported
public diagnostic API exists; the public loaded implementation identity remains
authoritative.

## Native Git witnesses and repository content

`GitService.observe()` owns the native Git witness used by init, plan, update,
review, migration, Fleet attempts and managed-workspace lifecycle. Git branch,
HEAD, status and changed paths retain Git's scope. A rename includes its source
and destination for allowed-write checks; a copy includes its changed destination.
Malformed or truncated successful status output is an observation failure.
Unavailable status is never sufficient for a clean-tree or allowed-write gate.
Git staging, commits, cancellation and uncertain publication stay with Oh-Goon.

| Evidence | Owner and accepted use |
| --- | --- |
| Git position, status and changed paths | Native Git execution preconditions, allowed writes and operation reporting |
| `pyproject_sha256`, `uv_lock_sha256`, legacy `state_id` | Oh-Goon operation/attempt provenance and manifest preconditions; historical fields retain their definitions |
| Selected source identity, source freshness, dependency qualification, impact and verification relevance | Hashmarks, consumed through the public integration API |
| Captured source, execution custody, outcome and recovery | Existing Oh-Goon source-admission, Fleet, Journal and reconciliation owners |

The legacy fingerprint samples Git status and two files sequentially. It is not
an atomic source snapshot and does not identify arbitrary repository bytes.
`RepositoryEffectFence` and the strict Fleet post-run Git guard check these limited
Git/manifest conditions; captured source admission separately uses Hashmarks
content identity. Equal fingerprints
cannot prove that an uncertain worktree effect did not happen: a second source
edit in an already dirty tree, or a write followed by restoration, can leave the
fingerprint unchanged. Without a canonical operation report, such equality keeps
worktree-mutation recovery `INDETERMINATE` and blocks automatic replay. Recorded
historical reconciliation results remain readable without reclassification.

No separate Hashmarks Git API is required for these witnesses. Its existing
`RepositoryIdentity.observe_inputs()` and `observe_directory()` establish scoped
filesystem evidence; they do not replace Git refs or authorize Git lifecycle.
For a single-file content identity, Oh-Goon retains both `hash_file()`'s digest
and its hashed-byte count from the same producer result, without a later stat
that could describe a different file revision. This low-level streaming digest
does not claim stable selected-input observation or repository-wide freshness.

## Runtime/source correlation

Oh-Goon captures operational observations and binds them to the original
Run/Step/Attempt. Interpreting which repository member, line, symbol or dependency
that observation refers to is Hashmarks-owned repository intelligence, consumed
only through the public `oh_goon.integrations.hashmarks` API. A bounded request
uses the admitted source identity/revision and already-known workspace/path
evidence; responses retain provenance, freshness, completeness, ambiguity,
unsupported state and omissions. Unavailable producer precision remains unknown
or refused, never repaired with ambient Git, filesystem or traceback heuristics.

Game Tape, diagnostics and Scout may project those qualified facts beside
execution evidence. Correlation obtained after admission cannot change the active
Run's frozen verification membership, acquire custody or establish a terminal
result. A supported producer capability and its consumer conformance must exist
before that correlation is advertised; this boundary does not claim a new runtime
correlation feature.

## Relevant development admission

The development check owner consumes supported Hashmarks verifier nominations
for the captured Git change set. Direct and bounded indirect source-qualified
links remain distinct from owner-completeness or negative evidence. The supported
producer's `verification.changed_set` operation uses `limit=2` for each changed
member within one native root/lifetime. A global two-target cap cannot drop
another member's proof, and the consumer does not rank or rebuild relationships
from ownership graphs. Hashmarks retains selection authority and its original
ambiguity, omitted-reference and truncation qualifications.
Each observation retains its complete native changed-set packet. The ordinary
executable projection omits duplicate contexts and relationships; support and
dependency admission retain their consumed relationship views. Canonical packet
facts and exact membership remain unchanged.
Required policy proofs and changed-test nominations remain explicit. One native
map/read session serves the supplied paths; producer bounds and source-member
provenance remain in the bounded selection artifact. Oh-Goon applies accepted
fast/daily cadence and native result admission; it does not infer another impact
graph. Missing consumer proof stays SELECTION_UNVERIFIED or uses a specifically
justified bounded execution-policy fallback. That policy cannot supply missing
repository meaning or strengthen unknown producer scope. Policy protection is a named execution-policy
obligation, not a Hashmarks-inferred relationship. See the
[CI contract](../CI_CD_CONTRACT.md#repository-checks-on-github).

Native reconciliation and task preparation happen before the composite read scope.
Dependency observation captures its bounded source inventory once and reuses
those reconciled native contributors. The read constructs fresh observations after preparation;
stale packets are not repaired or relabeled as current.

Backend runtime dependency changes use the released producer's public lock
before/after delta and dependency-consumer evidence. The adapter supplies selected
distribution `METADATA` and `RECORD` from the native uv environment; package-name
similarity does not establish import ownership. Consumer queries use an explicit
inventory of admitted `backend/src` Python files from `native.files`. Hashmarks
owns the module correspondence, import observations, changed-set relationships
and nominations; Oh-Goon retains those facts and applies the existing execution
admission. Each chain keeps the consumer source's intact changed-set packet; the
changed lock is connected by retained dependency evidence. This does not introduce another
resolver or impact graph.

Pytest-domain file nominations remain advisory until the existing native collector
establishes executable members under the admitted configuration and cadence. The
development owner preserves native selected/deselected members and empty advisory
targets alongside the raw producer evidence. Empty support-file nominations cannot
replace a component's required chain to a collected verifier. The execution batch
owner independently collects the admitted files and accounts for exact native
execution membership; the trusted-base gate also checks the plan's file set.
For changed Python sources, the trusted base independently observes ordinary
native membership once in the existing backend, tooling and cross-owner
contexts, then requires exact passed selected cases from changed files. This
does not broaden local focused planning or execute deselected journeys. The
configured frontend's positive self-nomination policy is a logical obligation,
not actual native collection or a claim over fixture-only projects. Other
unavailable native project contexts stay explicitly unverified through the
existing planner; source-role syntax cannot establish membership or absence.

Every changed existing runtime component must have a positive qualified
consumer-to-verifier chain. The aggregate `consumer_scope` stays incomplete with
`negative_evidence = "not-admissible"`: a selected chain does not prove complete
consumer coverage, runtime import closure or repository-wide absence. A single
existing replacement may introduce up to 32 unique required selections when native
path evidence and unconditional runtime edges bind each selection directly to the
upgraded, imported parent, itself required by an unconditional application-root
edge. The chain retains both selection identities and the
parent's qualified source/verifier evidence; it does not invent direct imports of
the introduced package. Other component additions, removals or forks,
dependency-policy changes, unavailable or
mismatched metadata, stale/truncated required delta or consumer observations and
missing chains remain unverified. A changed-set packet's aggregate search may be
incomplete, truncated or ambiguous while an individual native edge positively
identifies its source and target. Admission retains the raw producer qualifications and
uses only those exact positive relationships; it does not upgrade scope to complete.
The CI contract names the additional native lock, Python capability
and package-build obligations. These nominations retain repository-evidence-only
authority and enter the existing trusted-base proof protocol; they grant no
execution authority or producer-identity exception.

The trusted controller independently observes dependency admission while its
existing candidate-locked native environment is alive. Only immutable base
admission code runs there; candidate repository source is evidence input. The
loaded producer must match the admitted identity, and the fresh chains must point
to actually executed verifier files. Fresh installation metadata and native
observation identities remain separately proven rather than being relabeled as
the candidate process's earlier observations.

Documentation images use the same boundary. Hashmarks' native
`verification.changed_set` projection supplies source-bound content kind, byte
count, media signature, LFS pointer state and observed consumers. Oh-Goon retains
those facts unchanged and applies the named **bounded documentation-asset policy**
to additions and current modifications under `docs/assets/`: observed binary image
bytes up to 1 MiB, at least one syntax-qualified Markdown image or link consumer,
and no observed product, test, build or unqualified consumer. Every supporting
member must be present with a current observed revision. Incomplete source reads,
candidate or byte bounds, unresolved pointers and absent bytes cannot qualify.

This policy does not establish repository-wide absence of consumers. The producer's
global consumer scope remains `unknown` and negative evidence remains inadmissible;
its source coverage and truncation describe the bounded observed candidates.
Packet-level verifier truncation and repository-wide freshness remain separate
qualifications. Oh-Goon binds admission to the captured candidate before and after
the native read, and the immutable base controller independently reobserves the
asset and supporting sources using its own installed, trusted Hashmarks. Candidate
evidence must equal that observation; no candidate parser or validator is imported.
Both observations first reconcile discovery once through Hashmarks' native index
owner, so a cached map cannot omit a newly added indexed reader.

A media signature is not an image integrity, dimensions, rendering or authenticity
check. There is no local PNG parser or filename exception. Git/LFS materialization
owns acquisition: an LFS pointer identifies expected object metadata, while
hydrated image bytes are required for this policy. Deleted assets and the absent
side of a Git rename still require separate base-side evidence and remain
`SELECTION_UNVERIFIED` when no qualified verifier covers them.
