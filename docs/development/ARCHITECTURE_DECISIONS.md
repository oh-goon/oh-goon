# Current architecture decisions

This document contains structural decisions that remain current. Superseded
phase-by-phase records remain in prior checkpoints or Git history. Executable
architecture checks enforce current ownership contracts; an older check does
not redefine a decision merely because it still passes.

## Fail-before-work and certification authority

### The valid path is the simplest path
If an invalid state is already knowable by its responsible authority, reject it before workload execution begins. Do not add ceremony to valid execution merely to make invalid paths detectable.

### Authority is resolved once and transported, not repeatedly rediscovered
Platform and repository-native authority are established by their owning contract and transported to execution. Downstream Goons, Steps, launchers, and certification checks consume that authority; they do not independently re-resolve the same fact from PATH, ambient configuration, or duplicate declarations. Explicit workload authority is reserved for genuinely additional capability.

### Certification proves semantics, not source shape
Physical source size, source line position, formatting, lint/debt budgets, documentation shape, and similar repository-quality proxies do not determine APP certification. Useful maintainability observations belong to diagnostics unless a direct semantic invariant is proven. Harmless source movement must not change certification.

### Tactical containment is not permanent architecture
A candidate may use a narrow tactical declaration or compatibility repair to close a reproduced failure. Such containment does not become architectural precedent merely because CI is green. Follow-on cleanup must either justify independent semantic ownership or derive/delete the temporary mechanism.

### Runtime-only failure is not a reason for speculative preflight
Facts that cannot legitimately be known before execution remain native runtime failures. Oh-Goon must not create shadow preflight/resolver systems to predict them.

The detailed normative change-review and anti-drift rules are in `FAIL_BEFORE_WORK.md`.

## Execution and repository-effect ownership

### Repository effects have one owner-neutral durable lifecycle
A repository effect is claimed, started, settled and reconciled through one durable lifecycle independent of which executor implementation performed it. Durable facts, not process memory, determine recovery.

### Live execution ownership is non-stealable by timeout
A timeout may classify an owner as unavailable, but another worker cannot silently steal a live effect merely because time elapsed. Recovery must follow the durable coordination contract.

### Ownership-critical writes share the lease-check transaction
Writes that depend on current ownership prove that ownership in the same transaction as the mutation. A stale lease cannot race a successful state transition.

### FleetRun owns orchestration, not repository-effect recovery
FleetRun coordinates the target set and overall outcome. Per-repository uncertain effects remain owned by the repository-effect recovery lifecycle rather than being replayed by Fleet orchestration.

### Schedule FleetRun lineage is derived, not persisted
Schedules retain their durable prepared target. Individual FleetRun lineage is derived from actual occurrences/runs rather than maintained as a second mutable lineage store.

### Successful operation evidence retires the effect coordination row
Once canonical success evidence is durably committed, mutable coordination is no longer the authority and can be retired.

### Schedule has one durable active state
A schedule does not maintain overlapping hidden notions of enabled/running/active ownership. One durable state determines whether future occurrences may be produced.

### Read-only reconciliation is not a durable attempt state
Inspection/reconciliation may classify evidence without creating a fictitious executable attempt.

### Recovery classifications and acknowledgements are Journal evidence
Recovery decisions that affect future execution are durable facts and belong in the Journal rather than transient logs.

### Mutable repository-effect rows represent unresolved coordination only
Mutable rows are coordination state while an effect is unresolved; completed evidence is immutable run/effect evidence.

### Recovery mutation evidence participates in result freshness
If recovery changes execution-relevant state, downstream freshness/result identity must see that mutation rather than serving an older supposedly-current result.

## Skill source ownership

`.goonskills.yml` beside the root Plan/Goon owns local names and grouped Git
source/ref/path declarations. Native Git acquires each declared collection;
`goon.lock` owns its exact resolved commit. Cache directories are replaceable
materializations, not version selectors. Normal runs and repair recaptures use
locked commits, including after a cache miss. Only explicit `lock --update-skills`
refreshes moving selectors. INI and Gitlink registry modes are removed.

The existing Agent source capture reads complete selected directories once,
and the existing handoff transports those bytes. Skill acquisition creates no
agent runtime, permission grant or native-agent configuration owner. Plan/Goon
Steps continue to own execution order and task inputs. See [Agent Skills](../AGENT_SKILLS.md).

## Verification and strict-executor authority

### Ordinary execution defaults to trusted local verification
Omitted Plan/Goon execution settings select verification. Public application construction
and CLI Plan runs default to local execution and wait for completion, using the existing
Fleet/Step lifecycle. Configuration validation, deadlines, cancellation, durable outcomes
and cleanup still apply. Independent signer custody, fs-verity and privileged enrollment
belong to explicitly selected strict execution, for untrusted code or additional guarantees.
An explicit deployment provisioner selects strict admission; `--strict` also selects that
profile. Missing strict authority fails before workload and never falls back to local use.
Provider/model setup is separate and uses the caller-selected JSON or TOML catalog.

### APP verification has no strict placement authority
`execution.purpose: verification` consumes the already-initialized repository and durable certification control state. It does not manufacture, inherit, validate, materialize, export, or require strict release/CI executor inventory, certification cgroup overrides, or certification systemd-delegation markers.
The certification frontdoor must classify verification purpose and remove inherited strict placement authority **before static admission**, so no APP admission, bootstrap, or orchestration layer can accidentally observe an unrelated strict executor control plane.

That prohibition applies to the complete APP Plan, not only its bootstrap. In particular, `oh-goon/local-runtime` owns functional backend/Vite startup, local-access/proxy proof, and deterministic teardown through `make local-runtime-verification`; it must not reacquire strict authority inside the Goon. Production-shaped repository Goon execution, physical cancellation, crash/restart, and strict parallel Shared Plan acceptance are separate strict real-world/release owners.

### Strict placement is purpose-selected
Strict executor/cgroup authority is requested only by an explicit workflow purpose that can create strict CI/CD workload attempts. The release path opts in; APP/self verification does not. Delegated systemd re-entry is valid only for that explicit strict purpose and cannot be used as an alternate APP frontdoor.

### Host mutation is named for the authority it prepares
The explicit WSL/systemd host mutation capability is `make strict-executor-setup`. There is intentionally no `make certify-setup` alias because that name implies a false APP prerequisite and invites the removed coupling to return.

### Control-plane authority does not leak into workload children
Certification executor inventory, certification cgroup overrides, and certification systemd-delegation markers are process-control authority. They are consumed by their owners and stripped before arbitrary workload child birth unless a dedicated strict-execution owner explicitly transports the required capability.

## Fleet and admission structure

### Git is optional repository-native metadata, not source authority

An explicitly selected configured source root may participate in Plan verification or strict
execution without Git metadata. Catalog, filesystem subject, source content, workspace,
admission, and physical custody identities answer different questions:

| Fact | Canonical owner |
| --- | --- |
| Stable catalog identity for a canonical path | [`core/product_identity.py::repository_id()`](../../backend/src/oh_goon/core/product_identity.py) |
| Git-specific stale-observation token | `core/product_identity.py::repository_instance_id()` |
| Git-optional execution subject token | `core/product_identity.py::repository_subject_instance_id()` |
| Repository-evidence/content identity and freshness | Hashmarks through [`integrations/hashmarks/`](../../backend/src/oh_goon/integrations/hashmarks/) |
| Admitted subject plus captured source | [`RepositorySourceAuthority`](../../backend/src/oh_goon/core/models/source_authority.py) and the existing [repository/Plan admission owner](../../backend/src/oh_goon/integrations/fleet_plan_preview.py) |
| Durable workspace allocation, state, checkpoints, and leases | [`runtime/workspaces.py::RepositoryWorkspaceAuthority`](../../backend/src/oh_goon/runtime/workspaces.py) and its existing runtime collaborators |
| Validation of supplied execution authority | Existing [Fleet admission](../../backend/src/oh_goon/services/fleet_admission.py) and [Enforcer](../../backend/src/oh_goon/core/enforcer_lace_semantics.py) owners |
| Active physical execution custody | Existing LACE and [process-isolation owners](../../backend/src/oh_goon/integrations/fleet_plan_process_isolation.py) |

Fleet instance fencing uses the repository-subject token, which includes Git metadata when
it exists and remains defined when it does not. Git-derived inputs and Git Steps still require
native Git at their own owner; certification, release, source-ZIP acceptance, and strict admission
must not manufacture or require Git merely to make otherwise-valid source executable.

Overwriting an existing file in place can change content identity while leaving the subject
token unchanged. Adding or removing root directory entries can also change the token through
root ctime. Metadata tokens fence observations; they do not retain file descriptors or establish
continuous physical custody. Restart recovery must re-prove its durable bindings through the
existing lifecycle/recovery owners; a persisted token does not preserve process or descriptor
custody. A workspace ID identifies its durable allocation: stale references, lifecycle versions,
checkpoints, assignments, leases, or physical bindings can be rejected without replacing that ID.

### FleetRun lifecycle state is structural
FleetRun state is represented with explicit structural states rather than loosely related booleans or implicit event-position assumptions.

### Tagged-union discriminators are required end to end
Where execution modes have distinct semantics, their discriminators remain explicit through model, persistence, API and frontend boundaries.

### Strict CI/CD admission is structural and target-set atomic
Strict CI/CD validates the entire frozen target set and execution requirements before admission. There is no partially admitted CI/CD run.

### Repository source authority is explicit and mode-independent

Every Fleet Plan repository admission carries one first-class `RepositorySourceAuthority`. It
contains the exact repository-subject instance identity and filtered source snapshot admitted
before effects. The semantic Plan preview identity contains only repository-resolvable Plan
semantics; source identity must never be hidden inside that preview hash. The durable
repository/Plan admission derives one admission identity from the semantic preview plus the
source authority. Direct verification and strict CI/CD must consume the same source-authority
shape; execution mode may add isolation/runtime evidence but must not redefine which source
was admitted.
Strict execution evidence may add physical `base_workspace`, runtime, isolation, and tool
evidence, but it must carry the admitted `source_authority_id`, and its frozen base workspace
must match the admitted source snapshot. The strict input owner checks the admitted subject
token before and after freezing and rejects a changed observed subject even when source bytes
match. These metadata observations do not establish continuous descriptor custody.
Success/certification/package consumers compare the
one durable repository admission authority for every execution mode; they must never branch
between direct admission proof and strict execution-input proof to answer which source passed.

### Frozen repository admission is revalidated and evidenced
The exact repository capture admitted for execution is durable authority and is revalidated where physical execution requires it. Evidence records the admitted identity rather than a newly discovered repository set.

### Authenticated replay requires native parent authority

For authenticated rerun and placement-loss readmission, a historical parent/child
Journal candidate is an observation, not permission to create a new native Run.
The existing native admission owner rejects those paths until an owned signed
binding proves the exact durable parent and child, frozen Plan/source/repository
identities, native issuer observations, signer epoch, lease, and transaction.
An ordinary child receipt cannot authenticate its parent. Read-only historical
lookup and exact idempotent admission replay retain their existing contracts;
they do not supply the missing new-Run lineage authority.

### Admission and physical evidence prove different facts

An authenticated contract binds the admitted policy and native observations.
It does not demonstrate backend containment, causal physical observations,
teardown, or quiescence. The selected execution backend and lifecycle owners
must supply those facts. Keep pure semantic tests, SQLite publication tests,
native execution, browser journeys, and release qualification distinct when
reporting results. None may inherit a pass from an older source checkpoint.

## Isolation, content and filesystem authority

### Destination-restricted egress is a kernel boundary
When destination-restricted network egress is requested, enforcement is provided by the isolation/kernel boundary, not by application convention inside the workload.

### Cross-run reuse is content-addressed, never mutable-keyed
Reusable content is identified by immutable digest/content identity. Mutable names, paths or latest-version keys do not establish safe cross-run reuse.

### Repository owner and retained filesystem authority are one capability
The executor that owns a repository effect also owns the retained filesystem authority required to finish or publish that effect. Ownership is not split into unrelated process and filesystem tokens.

### Strict attempt trees are re-proven before host-side post-process reads
Before host-side code consumes strict-attempt output, the retained tree and ownership boundary are proven again rather than trusted from an earlier observation.

### Remote result archives are staged and ownership-proven before replacement
Remote outputs are staged, validated and ownership-proven before atomically replacing the canonical destination.

## Paths and authoring

### Execution paths and authoring navigation are separate path domains
Authoring/template navigation paths help humans locate Plans/Goons; resolved execution paths identify runtime authorities. One must not be overloaded as the other.

## Refactor compatibility

### behavior and domain invariants, not private topology, define refactor compatibility
Private classes, helpers, call chains and module layouts may be simplified when observable semantics and declared ownership invariants remain unchanged. Tests and architecture checks protect behavior and authority, not obsolete implementation shapes.

## Generated contracts

Generated OpenAPI and frontend API files are checked-in contract source. The backend OpenAPI capture is generated once, frontend contract generation consumes that exact capture, and `check-generated` verifies byte-for-byte parity without rewriting the source tree.

## Plan, Goon and lock ownership

`plan.yml` owns human-authored execution intent and ordered Goon selection. `goon.yml` owns executable behavior, composition, inputs and requirements. One canonical resolver produces the immutable resolved execution. `goon.lock` is only its compact deterministic fingerprint. Enforcer/admission owns whether it may run, executor performs the already-admitted identity, and Game Tape owns what actually happened.
