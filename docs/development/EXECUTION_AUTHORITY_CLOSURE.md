# Execution Authority Closure

Oh-Goon's execution rule is strict: **if execution authority cannot be proven, the workload must not start**.

For canonical repository workloads, requirement discovery and permission are deliberately separate.
Make/script/npm analysis derives executable **need**; `backend/goon-execution-requirements.json` owns
only the independent `workload_tool_permissions` ceiling for each derived `make:<target>` owner.
Compiled canonical Steps own their own identity and execution semantics; the policy file does not
mirror them. A discovered executable never authorizes itself. Static
admission proves `need ⊆ permission`, then generated canonical tool-authority transport carries only
the exact need into the immutable Step. Runtime freezing and sibling-Step isolation are unchanged.

Native readiness is semantic, not predicted from installation shape: actual npm package scripts own
frontend package resolution, actual Playwright launch owns browser availability, and each
`oh-goon-qualification-exec` invocation owns its interpreter-binding proof. The removed npm graph,
Playwright filesystem and ceremonial qualification preflights must not return under new names.

## Current phase: Promotion-readiness strict tooling child authority closure

Canonical Steps are validated from project-owned execution source before certification delegation, mutable certification state, workspace preparation, or Fleet Run creation. The closure now follows static Make edges, reached project-owned shell scripts, and reached project-owned npm package-script chains.

For each annotated workload owner, Oh-Goon derives:

- static Make prerequisites and statically named recursive `$(MAKE)` edges;
- direct Make recipe executables and shell-required recipe structure;
- reached project-owned shell scripts and their exact source identities;
- shebang interpreter authority;
- statically reachable script branches when the controlling environment value is admitted and known;
- nested owned scripts;
- `sh -c` / `bash -c`, `env`, explicit `xargs`, `find -exec` / `-execdir`, pipelines/boolean segments, `trap` cleanup paths, and command substitutions;
- reached `npm run` / package-script aliases from project-owned `package.json`;
- locked package-local executable providers from authoritative `package-lock.json`;
- provenance from the owning Make target through each Make/script/package-script edge;
- unresolved dynamic executable edges that make the closure unprovable.

`OH_GOON_OPAQUE_TOOLS` is not a second complete authority table. It is only a bounded supplement for executable requirements behind external/opaque boundaries the project-owned analyzer cannot inspect. `OH_GOON_FORWARD_ARGV_FROM` similarly grants nothing; it only declares which argv position an owned wrapper forwards so the statically supplied child command can itself be analyzed.

Package-local executables are a separate authority class from host executables. A package bin is admissible only when its provider is a direct dependency and the provider/version/bin target are locked. If one bin name has multiple locked providers, admission fails unless `package.json` contains a narrow `ohGoonExecutionAuthority.packageBinProviders` disambiguation naming a direct locked provider. This is disambiguation only, not a second package-bin allowlist.

`npx` is rejected at this logical authority boundary because it can imply package acquisition or resolution not represented by the admitted locked script intent. npm/uv continue to own package dependency and supply semantics; Oh-Goon only proves the executable path the project intends to invoke.

A canonical Goon passes only when its declared Step capabilities close the derived + opaque host-tool requirement set and every reached package-local executable has proven locked project intent. Missing host authority is `TOOL_AUTHORITY_MISSING`; unresolved project-owned execution is `EXECUTION_AUTHORITY_UNPROVEN`.

The runtime sandbox remains an independent second fence. Static proof prevents known-invalid workloads from starting; runtime enforcement blocks divergence.

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

The provider receipt intentionally hashes only the admitted provider and target, not unrelated dependency trees. A change to an unrelated installed dependency therefore does not create false invalidation, while package-lock/source drift, provider mutation, target mutation, missing bins, retargeting, or dependency-root escape fail closed.

Explicit generated/owned shell Steps also bind every reached owned script byte after the attempt workspace exists. Their derived host executables must already fit the Step's frozen host-tool authority, and package-local children must already exist in the pre-execution logical package authority. Mutation after binding is rejected at the same prelaunch fence.

Negative qualification proves this refusal happens before `ProcessIsolationBackend.execute()`; a failed materialization proof launches zero workload processes.

Native qualification also exposed an analyzer/materialization boundary bug: after `make init`, generated uv console scripts under `backend/.venv/bin` existed and the source scanner could mistakenly parse their Python syntax as shell, inventing executable requirements such as `__name__`. 868 now treats dependency-manager materialization roots as supply rather than repository-owned shell source. Static authority must therefore be invariant before/after dependency materialization; focused regression tests materialize an actual uv-style wrapper shape and assert the same canonical tool closure and execution-authority identity.

## 869 runtime reconciliation closure

868 proves that the materialized executable provider/target still corresponds to admitted logical authority. 869 carries that authority through the process-isolation launch boundary as one `RuntimeAuthorityReceipt` owned by the command-execution layer. The receipt records the workload executable actually selected in the sandbox namespace rather than the outer transport executable.

For strict Bubblewrap execution, Oh-Goon resolves the exact sandbox workload path against the admitted PATH/toolchain/workspace/managed-input authority before the transport process is invoked. It verifies the frozen provider binding, hashes the actual executable bytes, and records any absolute shebang interpreter that is already part of admitted tool/runtime authority. A missing executable, PATH drift, undeclared provider, undeclared interpreter, or byte mismatch raises `EXECUTION_AUTHORITY_DIVERGENCE` before workload launch.

For remote OpenSandbox execution, protocol v2 reconstructs the exact admitted root/toolchain/workspace, independently resolves and hashes the workload executable and shebang interpreter inside that root, and returns only the independently observable runtime subset. The host compares that observation to its admitted/materialized expectation. Provider identity, runtime-root identity, and materialization identity remain host-owned authority facts and are never trusted merely because the remote side echoes them. Missing or mismatching remote observation fails closed. Once observation has succeeded, timeout/failure results preserve that observation so execution outcome is not confused with authority divergence.

Runtime executable/interpreter identity is deliberately raw SHA-256 of the actual executable bytes. This is distinct from Hashmarks `SnapshotIdentity`, which remains the admitted provider/source identity. The two identities must not be substituted for each other.

**Historical 869 status:** this checkpoint remained pre-exec reconciliation rather than an atomic verification-to-`exec` handoff. The then-next TOCTOU/descriptor closure subsequently landed in 870/873 as documented below. Direct workspace executable symlinks remain fail-closed.


## 871 incremental revalidation economics closure

871 removes duplicate execution-authority work only where an immutable run-prepared identity already exists. Reuse is expectation/provenance, never terminal proof. Bubblewrap still hashes the exact sealed memfd bytes before execution and OpenSandbox still returns an independent observed runtime identity. Mutable attempt materialization remains cold-revalidated.

The phase adds diagnostic `ExecutionAuthorityEconomicsReceipt` evidence so reuse and recomputation are auditable without becoming authority. Exact-870 measurements show provider verification 2→1 and pre-seal raw hashing 2→0 for a standalone executable, and 4→2 / 4→0 for executable+interpreter, while terminal sealed proofs stay 1→1 and 2→2 respectively.

Native qualification also exposed two portability/source-hygiene boundaries now closed in 871: Linux memfd sealing must not depend on CPython exporting every `fcntl.F_SEAL_*` symbol, and Windows MOTW sidecars must never silently enter packageable source. `make init` may remove the latter for developer ergonomics, but source-layout, package construction, and archive identity fail closed if they reappear afterward.

872 closes **F8 / Game Tape / Why-Not diagnostics** for materialization, reconciliation, atomic handoff, economics, and divergence provenance. The next authority work should be driven by measured/native gaps rather than another predeclared authority store.

## 870 atomic verification-to-exec closure

869 reconciles the workload path and bytes immediately before launch. 870 removes the remaining race between that reconciliation and the kernel opening the executable.

At the original 870 checkpoint, local Bubblewrap copied the reconciled executable and any admitted shebang interpreter into sealed Linux memfds and attempted to mount those inherited descriptors through `/proc/self/fd/<n>`. That transport is historical and was superseded by 873 after native evidence showed Bubblewrap could close the descriptor before resolving an ordinary path bind. Current Bubblewrap transport uses FD-aware `--ro-bind-data FD DEST` plus `--perms`, while preserving the same sealed-byte authority lease. Both pathname replacement and in-place mutation of the original inode after binding remain irrelevant to the bytes executed.

OpenSandbox protocol v3 performs the equivalent sealed copy inside the private reconstructed root. The top-level executable crosses the final launch cut through `fexecve`. When the executable uses a shebang, the admitted interpreter is also sealed and restored from that descriptor immediately before the kernel resolves it. Remote observation from 869 remains required and is not replaced by the atomic lease.

Direct workspace executable symlinks remain rejected. Package-local npm `.bin` symlinks continue to use the dedicated 868 provider/lock/materialization proof. A future feature may admit additional symlink forms only with an equally explicit contained-target proof; 870 does not broaden authority.

Native qualification of 869 also exposed two source/fixture boundary regressions that are now guarded here: strict materialization tests must use repositories that actually contain every declared owned script, and canonical initialization removes only disposable Windows MOTW sidecars before source/layout checks can mistake them for authored package members.

## 872 F8 Game Tape / Why-Not diagnostics closure

872 makes the already-closed execution-authority chain inspectable without creating a second authority plane. The final physical attempt may carry a bounded `PlanExecutionAuthorityDiagnosticEvidence` projection composed from the existing materialization receipt, `RuntimeAuthorityReceipt`, atomic handoff result, and `ExecutionAuthorityEconomicsReceipt` evidence. The projection preserves those original receipt identities; it has no independent admission or execution authority.

The Game Tape Step Inspector exposes admitted/materialized source provenance, package executables and owned scripts, observed runtime executable/interpreter identities, atomic handoff mechanism and terminal-proof bytes, plus reuse/recompute economics. Historical or partial evidence remains valid: missing optional diagnostic arrays render as absent rather than changing execution meaning.

Authority refusal is projected separately from the durable `FleetRunFailure`. `EXECUTION_AUTHORITY_DIVERGENCE` produces a Why-Not diagnostic with the existing failure code/stage/message and a safe fresh-admission action. No Game Tape component retries, broadens PATH, grants a missing executable, mutates receipts, or persists a duplicate failure fact.

Acceptance requires that Game Tape authority diagnostics are strictly derived: the materialization/runtime receipt identities shown to the operator equal the original execution-layer identities, and run-level Why-Not facts equal the durable Fleet failure facts.

## Closure identity

The execution-authority identity binds the closed host-tool set, derived host-tool set, provenance, opaque supplement contribution, package-local executable authority, package provenance, unresolved edges, Makefile identity, reached owned-script source identities, and reached `package.json` / `package-lock.json` identities. Changing any analyzed authority source therefore invalidates previous admission evidence even when the resulting tool names happen to remain unchanged.

## Current canonical proof

Static admission currently proves:

- **45/45 executable Step host-authority closures**;
- **0 unresolved project-owned execution edges**;
- **6 distinct locked package-local executable authorities**.

The six current package-local bins are `depcruise`, `knip`, `playwright`, `tsc`, `vite`, and `vitest`. `playwright` is explicitly disambiguated to direct locked provider `@playwright/test`; the other five are unambiguous from project source and lock evidence.

## Remaining phases

1. **Incremental economics** — cache closure/materialization/reconciliation/atomic-binding analysis only by source/Step/analyzer/materialization/runtime identity; identity mismatch always recomputes.
2. **Game Tape / Why-Not — CLOSED IN 872** — materialization/runtime/atomic/economics receipts and divergence provenance are exposed as derived operator diagnostics; missing authority is never auto-granted.

## Explicit non-goals

Execution-authority analysis does not become Python/Node module dependency analysis; uv/npm retain package dependency and supply ownership. Executable authority also does not imply filesystem or network authority. A Step permitted to execute `curl` does not thereby receive egress, and permission to execute `rm` does not expand its filesystem mutation scope.

The 868 materialization proof establishes the logical-provider-to-physical-target mapping. 869 additionally reconciles the selected workload path, bytes, PATH/environment authority, and admitted shebang interpreter immediately before launch (and independently inside the OpenSandbox reconstructed root). 870 adds the atomic sealed-descriptor handoff from verification to kernel execution; unproven direct workspace symlinks remain fail-closed rather than being partially admitted.

External tools may internally spawn implementation-specific children that project source cannot prove statically. Those boundaries must be modeled explicitly or remain runtime-enforced; they must not be silently inferred as project-owned source closure.

## Acceptance invariant

For every canonical executable Step, static admission reports a proven host-tool closure, zero unresolved project-owned execution edges, and proven locked intent for every reached package-local executable before the workload can enter execution. Negative qualification must prove that missing transitive authority, ambiguous package-bin intent, or unresolved owned execution launches zero workload processes.

## 873 Bubblewrap sealed-FD handoff repair

Native certification exposed a transport-layer gap after the 870 atomic closure. The sealed memfd itself was correct, but Bubblewrap was given `/proc/self/fd/<n>` as an ordinary `--ro-bind` source. Bubblewrap may close unreferenced descriptors during setup before resolving ordinary path mounts, so certification failed before workload launch.

873 keeps the same sealed bytes and terminal proof but declares each inherited descriptor to Bubblewrap through `--ro-bind-data FD DEST`, with `--perms` preserving the admitted execution mode. The descriptor is rewound immediately before spawn. This is a transport repair, not a relaxation: no mutable host path is reintroduced and no second authority fact is created.

## 878 promotion-readiness strict tooling child authority repair

A promotion-readiness strict-PATH re-proof found one host-dependent literal child inside the tooling-test workload: `test_release_foundation.py` launched external `true`, although `make:test-tooling-complete` owns only dynamic `bash` and `sh` authority beyond the primary `make` Step. 878 replaces that child with already-admitted `sh -c 'exit 0'`; it does not add `true`, broaden PATH, or create a new authority class.

A permanent AST regression scans literal Python `subprocess` children across the complete `scripts/tests/test_*.py` surface. Unguarded literal children must fit the real tooling workload authority or be the primary `make` executable. The optional Node relocation regression is admissible only behind its source-local availability check and pre-launch `pytest.skip`. This closes the failure class rather than special-casing the one observed executable.

