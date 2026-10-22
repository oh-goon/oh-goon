# Native tool authority

## Rule

**Oh-Goon orchestrates native tools; it does not reimplement their semantics.**

When an external or platform-native tool already owns an operation, that tool remains the semantic authority for the operation. Oh-Goon may select, invoke, isolate, bound, sequence, observe, and report the native operation, but it must not create a second implementation, alternate semantic model, shadow state machine, or substitute proof of the same behavior.

This rule is repository-wide. It is not limited to package managers.

Examples include:

- `uv` for Python interpreter discovery, dependency resolution, locking, synchronization, environments, native cache behavior, build/install operations, and registry interaction;
- `npm` for frontend dependency resolution/materialization, `package-lock.json`, package scripts, and npm-owned cache behavior;
- `pytest` for Python test collection/execution/result semantics;
- Ruff for formatting and lint semantics;
- mypy for type-checking semantics;
- Docker/BuildKit for image build/container/image/network semantics they natively own;
- Kubernetes and its API/CLI clients for workload, rollout, resource-version, scheduling, health, and cluster-object semantics they natively own;
- Git for repository history, commit/tree/index/worktree/merge/patch semantics;
- operating-system process facilities for process creation, signals, process identity, exit status, descriptors, and process-tree facts;
- native filesystem, archive, cryptographic, signing, and platform tools where they already own the requested operation.

The named tools are examples, not an allowlist. The same ownership rule applies to any native tool introduced later.

## Authority before declaration

Do not turn executable discovery into declaration bureaucracy. Before adding an explicit tool requirement, classify the dependency:

1. **platform authority** — already part of the supported Oh-Goon/executor runtime contract;
2. **repository-native authority** — already implied by committed project authority and its selected native workflow;
3. **exceptional workload capability** — genuinely new information not implied by either of the above.

Only the third category normally deserves a new workload declaration. A declaration is not stronger merely because it repeats a fact already owned by `pyproject.toml`/`uv.lock`, `package.json`/lock authority, a native wrapper, or the executor platform contract.

For example, `uv run pytest` does not make Oh-Goon the resolver of `pytest`; uv owns resolution inside the locked project environment. Likewise, a repository whose native Node workflow establishes npm should not need each downstream Goon or Make target to independently rediscover npm.

When a hidden executable is found, repair in this order: remove an unnecessary dependency; use the already-selected native workflow; align implementation with the existing platform contract; derive from existing repository authority; only then add explicit capability authority when it conveys new information.

A tactical explicit declaration may close a concrete candidate failure, but it must not silently become permanent architecture when the higher-level authority should ultimately imply the tool. See `FAIL_BEFORE_WORK.md`.

## What Oh-Goon may own

Oh-Goon may add behavior around a native operation only where the behavior is an Oh-Goon responsibility. Legitimate orchestration includes:

- deciding **when** an operation runs and in which ordered Step;
- transporting already-resolved inputs to the execution owner without re-resolving them;
- enforcing repository, workspace, sandbox, executable, environment, credential, network, and mutation boundaries;
- selecting an already-authorized target, executor, repository, Plan, Goon, or Step;
- applying an Oh-Goon-owned execution bound such as cancellation or a timeout;
- recording the exact command, cwd, environment authority, tool identity, exit status, stdout/stderr, and other evidence;
- binding evidence to source, lock, Run, attempt, executor, generation, repository, and Step identity;
- sequencing multiple native operations;
- classifying the observed result as `PASS`, `FAIL`, or `INCOMPLETE` without changing the native result;
- proving an Oh-Goon-specific isolation, authority, identity, provenance, or certification invariant that the native tool does not own.

Those responsibilities do not make Oh-Goon a co-owner of the native operation's semantics.

## Forbidden shadow authority

Oh-Goon must not:

- implement a second resolver when the native tool owns resolution;
- maintain a competing semantic cache when the native tool owns caching;
- parse or edit a native format in order to reproduce a semantic operation already provided by the native tool;
- reconstruct native lifecycle or state from an Oh-Goon mirror when the native authority can be observed directly;
- replace a blocked native semantic command with a different command and claim the same proof;
- narrow or broaden the native operation merely to manufacture a successful result;
- infer a native fact from adjacent evidence when the native tool itself owns the fact;
- duplicate native install, build, retry, rollout, scheduling, networking, formatting, linting, testing, type-checking, merge, patch, or dependency algorithms;
- turn unavailable native evidence into a synthetic `PASS`.

If the native authority cannot complete because infrastructure, credentials, connectivity, permissions, or required native state are unavailable, preserve that boundary. Required evidence remains `INCOMPLETE` when the native operation could not establish it.

## Diagnostics are not gatekeepers

Read-only native-tool diagnostics may remain available as explicit troubleshooting surfaces. They do not become mandatory preflights for a release, container, development, or certification operation that is about to invoke the same native tool. If the next semantic operation is `npm`, `uv`, `git`, Docker, or another native command, that operation owns whether it can start and what result it produces. Oh-Goon records that result instead of probing the same fact first.

A diagnostic command may answer a separate operator question. It must not be inserted into the normal path merely to predict whether the following native operation will succeed. Post-materialization smoke tests are also distinct: they prove the artifact that Oh-Goon just created can launch, rather than predicting a later operation.

## Exact-command retries

Retries are orchestration only when retrying is itself Oh-Goon policy and the semantic operation remains unchanged. A retry must not become a hidden fallback: command meaning, scope, target, cwd, resolved inputs, and authority-bearing configuration remain the same. Prefer the native tool's own retry facilities when they already provide the required policy; do not multiply retry layers without a demonstrated Oh-Goon-specific reason.

## Observation is not replacement

Oh-Goon may hash, record, or project native outputs after the semantic owner has produced them. Such evidence does not become a second semantic authority.

Examples:

- hashing `uv.lock` binds exact committed bytes; the hash does not resolve Python dependencies;
- recording a Git commit/tree identity binds source; Oh-Goon does not recreate Git history semantics;
- recording a Kubernetes resource version binds the observed object; Oh-Goon does not become a second Kubernetes scheduler or rollout controller;
- recording an OCI digest binds an image; Oh-Goon does not recreate Docker/registry image-resolution semantics;
- recording a process ID/start identity binds an observed process; Oh-Goon does not infer process liveness solely from its own status mirror.

## Delegation invariant

This rule composes with the repository-wide delegated configuration invariant:

> **Resolve once, transport once, execute once, report once.**

For release assembly this means one `release-locks-check` owns the pre-materialization
uv/npm lock-validity decision. Release acceptance delegates to the build path instead
of probing the same locks first, and release-version identity checking observes version
coherence rather than re-running package-manager compatibility. Locked materialization
is still a distinct native operation and must retain `--locked` fail-closed semantics.

For an already materialized Python project environment, native uv owns requirement and
Python-version compatibility. Oh-Goon may observe application-specific runtime facts
such as installed distribution identity, importability, required APIs, or ABI behavior,
but must not parse requirement minima and issue a second compatibility verdict.

For a strict repository-backed `uv:` Step, the admitted source is a new isolated
execution subject. The live checkout's `backend/.venv` is never authority and is never
transported. Admission proves Oh-Goon-owned source, project, group, execution and isolation
structure without running a disposable dependency resolution. Strict preparation is the
first native dependency operation: `uv sync --locked --no-default-groups --group <selected>`
acquires that exact project into a disposable preparation environment and one retained
run-owned cache. The physical attempt materializes an attempt-owned environment outside the source workspace
offline from that admitted cache, then executes the application command with native
`uv run --frozen --no-default-groups --no-sync`. Oh-Goon owns source identity, cache/workspace placement,
sandboxing and evidence; uv owns lock/group validity, distribution acquisition,
environment construction, console-entrypoint resolution and execution.

Raw references to `backend/.venv/...` or `frontend/node_modules/...` are therefore not
"already satisfied" tool authority for a strict repository workspace. They are an
untransported package-manager materialization dependency and admission rejects them
unless execution is represented by the corresponding native package-manager model.

The semantic owner resolves native meaning once. Oh-Goon transports the resulting authority, invokes the native owner once, and reports the observed result once. Re-entry or delegation must not create a second resolver or execution owner.

Ordinary trusted-local commands retain the resolved native executable path and
their original invocation arguments. Native tools may use the kernel executable
name for dispatch or respawn themselves; replacing that path with an anonymous
descriptor changes their semantics. The command owner records the observed
executable digest and environment authority without claiming immutable execution
materialization. Parent-death handling, cancellation, deadlines, output bounds,
and process-tree cleanup still use the same supervisor.

Callers requiring exact immutable local execution explicitly select
`preserve_native_executable_path=False`; the command owner then seals and executes
the captured bytes. Durable shard execution retains this selection because its
resumable evidence requires exact materialization. Selected strict sandbox
execution retains its separate sealed-descriptor and physical isolation owners;
ordinary native-path observation grants none of those guarantees. A native tool
that requires a stable kernel executable path is incompatible with anonymous
descriptor execution and cannot be qualified by a successful ordinary run.

## Design test

Before adding code around a tool, ask:

> **Is this code enforcing an Oh-Goon-specific boundary, or is it reimplementing an operation the native tool already knows how to perform?**

If the latter, remove the shadow implementation and invoke the native authority directly.

When the distinction is unclear, the burden is on the new Oh-Goon layer to demonstrate an independent Oh-Goon-owned invariant. Convenience, a nicer error message, cache optimization, or a desire to make a failing native command green is not sufficient justification for duplicate semantic ownership.


## Hashmarks dependency and package-local execution

Runtime evidence, provider, inference relay, and delegation module helpers use
native Python isolated mode (`-I`), so the repository working directory and
ambient `PYTHON*` settings cannot select their
modules or dependencies. The command guard uses its trusted absolute script with
`-E -S`, preserving its sibling protocol import while ignoring ambient Python
configuration. These controls apply to the helper interpreter; the admitted
workload still receives its configured working directory and environment.
Installed distributions in the selected Python environment retain import
authority, without repository-path injection or a bootstrap fallback.

Hashmarks release production and standalone installation are Hashmarks-owned. The Oh-Goon backend selects a released Hashmarks wheel through `backend/pyproject.toml`; native `uv.lock` owns the exact dependency selection and `make init` materializes it once. Oh-Goon must not vendor, reacquire, unpack into diagnostics, or maintain a parallel release/provenance ledger.

Package-local frontend executables are resolved by native npm scripts such as `npm run dev` and `npm run browser:runtime`. Acceptance must not infer readiness from `node_modules/.bin` existence before invoking npm. Exact package-local executable inspection remains valid only at the strict Fleet executable-materialization security boundary.

Within one lifecycle subject, dependency materialization occurs once unless an explicit new isolated subject is created. A later phase must not use a second `make init` as repair for contamination introduced by an earlier phase.
