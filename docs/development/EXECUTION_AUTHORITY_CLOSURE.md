# Execution Authority Closure

Selected strict execution refuses unresolved execution authority before workload launch. Ordinary trusted-local execution retains its existing admission policy; authored source observations do not create a new strict prerequisite for local development.

This document describes tool permission, materialization and selected strict
physical-attempt contracts. These checks share the original Fleet admission
and execution owners; they are not another Run lifecycle or a requirement to select privileged
custody for ordinary trusted-local verification. The current owner path is
`FleetRunControlPlane → RunExecutionSupervisor → claimed Run worker → FleetRunExecution →
ExecutionSession → FleetTargetScheduler → Step/Attempt`.
The [supervisor contract](EXECUTION_SUPERVISOR.md) owns that fresh-process/atomic-claim
boundary. HUG-181/HUG-183 retain complete failure/drain and broader lifecycle qualification.
A strict Step executable receipt does not qualify that separate Run boundary.

Reusable repository meaning belongs to mandatory Hashmarks through the public
`oh_goon.integrations.hashmarks` API; private integration modules and producer
imports stay inside that package. OH-Goon owns capability ceilings, execution
admission, physical byte custody and results. Native uv/npm retain dependency
resolution and installation semantics. The source-analysis migration is governed
by the [SSOT requirements](OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md)
and [current Hashmarks handoff](HASHMARKS_EXECUTION_LAYER.md), not an exception
that lets this tool-policy document authorize a local repository-semantic
fallback. Unsupported producer facts remain explicit; a native runtime fact
such as effective shell expansion is not replaced by a source-syntax guess.

Numbered closure sections below retain the history and rationale of strict
attempt contracts. Their checkpoint measurements, proof counts and release
claims describe those original sources; they are not qualification of the
current checkout. Current implementation starts at the
[logical/materialized authority owner](../../backend/src/oh_goon/integrations/executable_materialization_authority.py),
[runtime reconciliation](../../backend/src/oh_goon/integrations/fleet_plan_runtime_authority.py)
and [atomic handoff](../../backend/src/oh_goon/integrations/fleet_plan_atomic_runtime_authority.py).

For canonical repository workloads, requirement discovery and permission are deliberately separate.
Producer Make/script/npm observations supply positive executable **need candidates**; `backend/goon-execution-requirements.json` owns
only the independent `workload_tool_permissions` ceiling for each derived `make:<target>` owner.
Compiled canonical Steps own their own identity and execution semantics; the policy file does not
mirror them. A discovered executable never authorizes itself. Static
admission proves `observed need ⊆ permission`, then generated canonical tool-authority
transport carries those exact candidates into the immutable Step. This subset proof
is not proof that native execution has no other children. Unresolved source scope
remains explicit. Runtime freezing and sibling-Step isolation are unchanged.

Native readiness is semantic, not predicted from installation shape: actual npm package scripts own
frontend package resolution, actual Playwright launch owns browser availability, and each
`oh-goon-qualification-exec` invocation owns its interpreter-binding proof. The removed npm graph,
Playwright filesystem and ceremonial qualification preflights must not return under new names.

## Logical tool authority

The original compiler owns Step argv, working directory, environment and permission.
Hashmarks owns authored Make/shell/npm syntax and source relationships. The public
integration supplies one native Make parse with separate selected-target source
projections: literal prerequisite edges, source fact indices, provenance, cycles
and unresolved facts. Oh-Goon consumes those projections without rebuilding a
prerequisite graph or expanding Make variables.

Shell command, static binding and invocation observations remain producer-qualified
source candidates. Conditional defaults, branch conditions, command substitutions,
unknown heads and opaque native option/operand bindings do not become effective
commands. Oh-Goon does not evaluate shell `if`/`case`, `fnmatch`, environment
defaults or Make assignments. Native Make/Bash/npm retain effective expansion,
resolution, execution and result authority. Explicit original Step argv and working
directory are transported directly; authored `cd` text cannot replace that authority.
Global Make assignments/conditionals, shell environment writes and arrays retain
native evaluation uncertainty even when selected recipe heads are literal. In
particular, Make parse-time shell effects and shell assignment substitutions
cannot disappear from admission scope merely because they are not a command head.

One bounded native member capture supplies product annotation input; one Make
declaration parse in the same original source lifetime verifies that exact member
revision. Selected roots share captured script/npm observations and custody hashes.
Both retained payloads and total traversal work are bounded: repeated cached DAG
visits consume the original operation's work budget, and exhaustion remains unresolved.
Closed source lifetimes cannot reactivate cached observations. Npm inputs and
owned scripts carry custody SHA-256 of their original bounded byte transfer,
verified against producer member revisions. Logical/materialization owners consume
those receipts; they do not pair old semantic facts with a later independent path
read. These hashes establish transported-byte custody, not Hashmarks identity or
repository freshness.

The existing `OH_GOON_OPAQUE_TOOLS` declaration supplies only a bounded positive
supplement behind an opaque native boundary. The existing
`OH_GOON_FORWARD_ARGV_FROM` declaration transports the already-supplied child
operands as conservative need. It grants nothing and does not prove the script
forwards them. Its original captured bytes and producer provenance remain bound,
and unresolved `$@` or conditional source remains unresolved after transport.

The npm source adapter consumes selected manifest scripts, dependency declarations
and lock-bin descriptors from Hashmarks. Oh-Goon retains its own narrow direct
provider eligibility and explicit `packageBinProviders` disambiguation policy.
Multiple providers remain ambiguous until that explicit policy selects an eligible
literal descriptor. A raw string bin, unknown npm flag operand or source-only
package location cannot establish a native executable binding. In particular,
`npm --prefix DIR run NAME` is unproven rather than target-free success; this
consumer does not implement another npm option resolver.
Source syntax also does not establish native npm script-shell or lifecycle
configuration. No effective `sh` default is invented; main-body/bin candidates
remain useful while that native configuration stays unproven. Declared pre/post
hooks and source `cd` cannot disappear from uncertainty. Native lifecycle operations
such as `ci`, `install` and `version` remain opaque to this source-only proof;
metadata flags such as `--version` do not establish configuration absence.
An explicit Step working directory supplies native package context. A unique
repository-wide script-name candidate remains explanatory source evidence, never
proof of the package native npm will select. An explicit missing context cannot
fall back to another repository package. Producer literals and original argv keep
their exact spelling; punctuation and quotes are not repaired into another command.
Plain `dash` uses the same source boundary as the other admitted shell roles.
An absolute pathname with a shell basename cannot establish an arbitrary binary's
native role or bypass the logical/materialization source checks.

Canonical catalog admission protects positive need against the independent tool
ceiling and checks generated transport freshness. It reports unresolved source
scope separately; it does not certify native closure from source syntax. Existing
strict Run preview and physical materialization refuse unresolved authority before
launch. Ordinary trusted-local execution retains its existing policy. No candidate
can add permission, replace frozen executable custody, bypass sibling-Step isolation
or strengthen incomplete producer evidence.

The selected strict sandbox remains an independent execution fence. It cannot
supply missing repository meaning or turn unobserved native semantics into a
complete source proof. Physical provider/symlink checks, exact byte revalidation
and prelaunch refusal remain with their original execution owners below.

## 868 materialization closure

The 867 logical closure is now carried into each strict physical attempt. Run preparation freezes the reached execution-authority source identities and package-local provider intent from the admitted source. After Fleet copies the exact source/published workspace into the attempt, 868 proves that those logical authority sources have not drifted and binds the physical executable materialization that is about to cross the process-launch cut.

For every admitted package-local executable on the strict POSIX runtime, Oh-Goon now requires:

- the owning project package directory to be explicit in package-executable authority;
- `node_modules/.bin/<name>` to exist as the expected npm symlink;
- the symlink target to remain inside that package's admitted `node_modules`;
- the resolved target to equal the exact locked direct provider/bin target selected by 867;
- the materialized provider `package.json` name, version, and bin metadata to match the logical provider authority;
- bounded content identities for the wrapper target, executable target, and provider tree; and
- revalidation of that immutable receipt immediately before the process-isolation backend is invoked.

These are scoped strict-attempt custody checks on admitted executable bytes,
not a second npm resolver or an ordinary package-script readiness preflight.
Normal developer setup and npm script execution still use their native owners.

The provider receipt intentionally hashes only the admitted provider and target, not unrelated dependency trees. A change to an unrelated installed dependency therefore does not create false invalidation, while package-lock/source drift, provider mutation, target mutation, missing bins, retargeting, or dependency-root escape fail closed.

Explicit generated/owned shell Steps also bind every reached owned script byte after the attempt workspace exists. Their derived host executables must already fit the Step's frozen host-tool authority, and package-local children must already exist in the pre-execution logical package authority. Mutation after binding is rejected at the same prelaunch fence.

Negative qualification proves this refusal happens before `ProcessIsolationBackend.execute()`; a failed materialization proof launches zero workload processes.

Native qualification also exposed an analyzer/materialization boundary bug: after `make init`, generated uv console scripts under `backend/.venv/bin` existed and the source scanner could mistakenly parse their Python syntax as shell, inventing executable requirements such as `__name__`. 868 now treats dependency-manager materialization roots as supply rather than repository-owned shell source. Static authority must therefore be invariant before/after dependency materialization; focused regression tests materialize an actual uv-style wrapper shape and assert the same canonical tool closure and execution-authority identity.

## 869 runtime reconciliation closure

868 proves that the materialized executable provider/target still corresponds to admitted logical authority. 869 carries that authority through the process-isolation launch boundary as one `RuntimeAuthorityReceipt` owned by the command-execution layer. The receipt records the workload executable actually selected in the sandbox namespace rather than the outer transport executable.

For strict Bubblewrap execution, Oh-Goon resolves the exact sandbox workload path against the admitted PATH/toolchain/workspace/managed-input authority before the transport process is invoked. It verifies the frozen provider binding, hashes the actual executable bytes, and records any absolute shebang interpreter that is already part of admitted tool/runtime authority. A missing executable, PATH drift, undeclared provider, undeclared interpreter, or byte mismatch raises `EXECUTION_AUTHORITY_DIVERGENCE` before workload launch.

For remote OpenSandbox execution, protocol v2 reconstructs the exact admitted root/toolchain/workspace, independently resolves and hashes the workload executable and shebang interpreter inside that root, and returns only the independently observable runtime subset. The host compares that observation to its admitted/materialized expectation. Provider identity, runtime-root identity, and materialization identity remain host-owned authority facts and are never trusted merely because the remote side echoes them. Missing or mismatching remote observation fails closed. Once observation has succeeded, timeout/failure results preserve that observation so execution outcome is not confused with authority divergence.

Runtime executable/interpreter identity uses raw SHA-256 of the actual executable
bytes at the execution boundary. Hashmarks repository/content observation identity states
what source the producer observed; OH-Goon custody/materialization identities
bind the exact admitted provider/source bytes transported and executed. These
facts have different scopes even when they concern related bytes. Neither raw
SHA-256 equality nor a shared snapshot representation lets one replace the
other. See the [identity owner map](ARCHITECTURE_DECISIONS.md#git-is-optional-repository-native-metadata-not-source-authority).

**Historical 869 status:** this checkpoint remained pre-exec reconciliation rather than an atomic verification-to-`exec` handoff. The then-next TOCTOU/descriptor closure subsequently landed in 870/873 as documented below. Direct workspace executable symlinks remain fail-closed.


## 871 incremental revalidation economics closure

871 removes duplicate execution-authority work only where an immutable run-prepared identity already exists. Reuse is expectation/provenance, never terminal proof. Bubblewrap still hashes the exact sealed memfd bytes before execution and OpenSandbox still returns an independent observed runtime identity. Mutable attempt materialization remains cold-revalidated.

The phase adds diagnostic `ExecutionAuthorityEconomicsReceipt` evidence so reuse and recomputation are auditable without becoming authority. Exact-870 measurements show provider verification 2→1 and pre-seal raw hashing 2→0 for a standalone executable, and 4→2 / 4→0 for executable+interpreter, while terminal sealed proofs stay 1→1 and 2→2 respectively.

Native qualification also exposed two portability/source-hygiene boundaries at
871: Linux memfd sealing must not depend on CPython exporting every
`fcntl.F_SEAL_*` symbol, and Windows MOTW sidecars must never silently enter
packageable source. The historical init cleanup is superseded: current repair
is explicit `make source-transport-clean`. Init, validators and packaging do not
silently clean source; physical admission fails while contamination remains.

872 closes **F8 / Game Tape / Why-Not diagnostics** for materialization, reconciliation, atomic handoff, economics, and divergence provenance. The next authority work should be driven by measured/native gaps rather than another predeclared authority store.

## 870 atomic verification-to-exec closure

869 reconciles the workload path and bytes immediately before launch. 870 removes the remaining race between that reconciliation and the kernel opening the executable.

At the original 870 checkpoint, local Bubblewrap copied the reconciled executable and any admitted shebang interpreter into sealed Linux memfds and attempted to mount those inherited descriptors through `/proc/self/fd/<n>`. That transport is historical and was superseded by 873 after native evidence showed Bubblewrap could close the descriptor before resolving an ordinary path bind. Current Bubblewrap transport uses FD-aware `--ro-bind-data FD DEST` plus `--perms`, while preserving the same sealed-byte authority lease. Both pathname replacement and in-place mutation of the original inode after binding remain irrelevant to the bytes executed.

OpenSandbox protocol v3 performs the equivalent sealed copy inside the private reconstructed root. The top-level executable crosses the final launch cut through `fexecve`. When the executable uses a shebang, the admitted interpreter is also sealed and restored from that descriptor immediately before the kernel resolves it. Remote observation from 869 remains required and is not replaced by the atomic lease.

Direct workspace executable symlinks remain rejected. Package-local npm `.bin` symlinks continue to use the dedicated 868 provider/lock/materialization proof. A future feature may admit additional symlink forms only with an equally explicit contained-target proof; 870 does not broaden authority.

Native qualification of 869 also exposed two source/fixture boundaries: strict
materialization tests must contain every declared owned script, and transport
sidecars must not become authored members. Current source-layout admission
rejects contamination until the explicit transport-clean owner repairs it;
initialization no longer performs that historical cleanup.

## 872 F8 Game Tape / Why-Not diagnostics closure

872 makes the already-closed execution-authority chain inspectable without creating a second authority plane. The final physical attempt may carry a bounded `PlanExecutionAuthorityDiagnosticEvidence` projection composed from the existing materialization receipt, `RuntimeAuthorityReceipt`, atomic handoff result, and `ExecutionAuthorityEconomicsReceipt` evidence. The projection preserves those original receipt identities; it has no independent admission or execution authority.

The Game Tape Step Inspector exposes admitted/materialized source provenance, package executables and owned scripts, observed runtime executable/interpreter identities, atomic handoff mechanism and terminal-proof bytes, plus reuse/recompute economics. Historical or partial evidence remains valid: missing optional diagnostic arrays render as absent rather than changing execution meaning.

Authority refusal is projected separately from the durable `FleetRunFailure`. `EXECUTION_AUTHORITY_DIVERGENCE` produces a Why-Not diagnostic with the existing failure code/stage/message and a safe fresh-admission action. No Game Tape component retries, broadens PATH, grants a missing executable, mutates receipts, or persists a duplicate failure fact.

Acceptance requires that Game Tape authority diagnostics are strictly derived: the materialization/runtime receipt identities shown to the operator equal the original execution-layer identities, and run-level Why-Not facts equal the durable Fleet failure facts.

## Closure identity

The execution-authority identity binds the closed host-tool set, derived host-tool set, provenance, opaque supplement contribution, package-local executable authority, package provenance, unresolved edges, Makefile identity, reached owned-script source identities, and reached `package.json` / `package-lock.json` identities. Changing any analyzed authority source therefore invalidates previous admission evidence even when the resulting tool names happen to remain unchanged.

## Historical closure cohort

The original checkpoint reported:

- **45/45 executable Step host-authority closures**;
- **0 unresolved project-owned execution edges**;
- **6 distinct locked package-local executable authorities**.

That cohort's six bins were `depcruise`, `knip`, `playwright`, `tsc`, `vite`, and
`vitest`, with `playwright` disambiguated to `@playwright/test`. These counts do
not establish current closure or enumerate current package authority. The
actual source, native lock, selected execution policy and exact-source checks
own that answer.

## Historical follow-up notes

The checkpoint called for identity-bound incremental reuse and recorded the
872 Game Tape/Why-Not projection work. Those notes do not select another active
campaign, create a cache owner or grant authority from a historical PASS.
Current reuse remains expectation/provenance rather than terminal proof;
diagnostic projections expose original receipts without acquiring their effect,
admission or result authority.

## Explicit non-goals

Execution-authority analysis does not become Python/Node module dependency analysis; uv/npm retain package dependency and supply ownership. Executable authority also does not imply filesystem or network authority. A Step permitted to execute `curl` does not thereby receive egress, and permission to execute `rm` does not expand its filesystem mutation scope.

The 868 materialization proof establishes the logical-provider-to-physical-target mapping. 869 additionally reconciles the selected workload path, bytes, PATH/environment authority, and admitted shebang interpreter immediately before launch (and independently inside the OpenSandbox reconstructed root). 870 adds the atomic sealed-descriptor handoff from verification to kernel execution; unproven direct workspace symlinks remain fail-closed rather than being partially admitted.

External tools may internally spawn implementation-specific children that project source cannot prove statically. Those boundaries must be modeled explicitly or remain runtime-enforced; they must not be silently inferred as project-owned source closure.

## Acceptance invariant

Canonical static admission reports positive source need, its independent permission
comparison, and unresolved source scope without relabeling that scope complete.
Selected strict execution requires its original resolved authority and byte custody
before launch. Negative qualification proves that missing permitted authority,
ambiguous package-bin intent and unresolved strict execution launch zero workload
processes. Ordinary trusted-local admission does not inherit an additional strict
qualification gate from these source observations.

## 873 Bubblewrap sealed-FD handoff repair

Native certification exposed a transport-layer gap after the 870 atomic closure. The sealed memfd itself was correct, but Bubblewrap was given `/proc/self/fd/<n>` as an ordinary `--ro-bind` source. Bubblewrap may close unreferenced descriptors during setup before resolving ordinary path mounts, so certification failed before workload launch.

873 keeps the same sealed bytes and terminal proof but declares each inherited descriptor to Bubblewrap through `--ro-bind-data FD DEST`, with `--perms` preserving the admitted execution mode. The descriptor is rewound immediately before spawn. This is a transport repair, not a relaxation: no mutable host path is reintroduced and no second authority fact is created.

## 878 promotion-readiness strict tooling child authority repair

A promotion-readiness strict-PATH re-proof found one host-dependent literal child inside the tooling-test workload: `test_release_foundation.py` launched external `true`, although `make:test-tooling-complete` owns only dynamic `bash` and `sh` authority beyond the primary `make` Step. 878 replaces that child with already-admitted `sh -c 'exit 0'`; it does not add `true`, broaden PATH, or create a new authority class.

A permanent AST regression scans literal Python `subprocess` children across the complete `scripts/tests/test_*.py` surface. Unguarded literal children must fit the real tooling workload authority or be the primary `make` executable. The optional Node relocation regression is admissible only behind its source-local availability check and pre-launch `pytest.skip`. This closes the failure class rather than special-casing the one observed executable.

