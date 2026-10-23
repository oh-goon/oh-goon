# oh-goon CI/CD contract 🧹

> Old hacky code, gone. If it doesn't validate, it doesn't run.

This document is the short product-level contract for local CI/CD execution. The detailed mechanics live in [`development/FLEET_PLANS.md`](development/FLEET_PLANS.md), while [Architecture](ARCHITECTURE.md) explains the current ownership boundaries.

## Repository checks on GitHub

The repository's [required-checks workflow](../.github/workflows/ci.yml) runs on
pull-request updates and draft/readiness transitions, pushes to `main`, and
merge-queue groups. Ready pull requests, `main` and merge-queue groups run all
seven full checks. Each full job materializes the committed uv/npm locks through
`make init`, then invokes one existing owner
check: `backend-check`, `frontend-check`, `tooling-check`, `architecture`,
`check-generated`, `test-integration`, or `build-release`. These are also the
stable GitHub check names. The integration target executes both backend and cross-owner integration
tests. The release check rebuilds the frontend and verifies the assembled wheel.
Make and the native tools retain command, dependency, and result authority;
GitHub owns hosted scheduling and merge enforcement.

Require all seven checks from the GitHub Actions app for `main`, require the branch
to be up to date, and apply those requirements to administrators without bypass.
Require changes through a pull request and disallow force pushes and deletion.
These are GitHub repository settings; the workflow file alone does not enforce
them. Keep existing stronger review requirements when updating the settings.

To configure the existing [`main` ruleset](https://github.com/oh-goon/oh-goon/rules/25700231),
open Settings → Rules → Rulesets and edit it rather than creating a duplicate.
Set enforcement to **Active** for the default branch, add **Require a pull
request before merging** and **Require status checks to pass**, select all seven
names above from GitHub Actions, and require the branch to be up to date. Keep
the bypass list empty and retain the deletion and force-push blocks. Enabling a
ruleset containing only deletion/force-push rules does not require PRs or checks.
The managed GitHub app can read ruleset metadata but cannot administer these
settings; verify the active rules and check names independently of a green
workflow run.

Draft pull requests run only the nonrequired `draft-feedback` job: locked setup,
lint, format, architecture and lock checks, without pytest suites. Mark a draft
ready for review to launch all seven full checks for its current commit; later
ready-PR pushes also run all seven. Returning to draft starts cheap feedback and
cancels superseded runs through the existing concurrency policy. Focused tests
remain the local iteration loop; draft feedback is not test or merge completion.

The full job's draft condition is evaluated before matrix expansion, as specified
by [GitHub's job-condition contract](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idif).
Its skipped draft stub is explicitly named `full-checks-pending-review`, outside
the seven required check names. Never conditionally skip a required check under
its ordinary name: [GitHub treats a skipped job as success](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions).
A new draft commit does not report the seven full checks; repository settings must require those
exact names. Workflow scheduling does not install or prove those settings.

Full required jobs have no path filters or allowed failures. Full Git history supplies
the PR-base quality-floor comparison. Hosted `backend-check` selects unit and
dependency-complete tests through Make's `BACKEND_CHECK_TEST_ROOTS`; the required
`test-integration` job owns backend and cross-owner integration tests. Together
they cover the entire ordinary backend suite once. Local `make backend-check`
retains pytest's complete default testpaths. Make passes the selected roots
directly in pytest's argv rather than `PYTEST_ADDOPTS`, so nested pytest processes
do not inherit an unrelated test-root selection. Both hosted pytest jobs stop at
their first failure and report the 40 slowest setup/call/teardown durations above
50 ms. A successful run
covers every ordinary test; no successful test receipts are reused across changed
source. Publish coherent changes after focused checks so superseded full runs can
be cancelled by the workflow's existing concurrency policy. Ubuntu 26.04 supplies the
native Bubblewrap overlay features used by the selected agent tests. Each full-check runner enables
unprivileged user namespaces for the ordinary Bubblewrap tests.
Hosted runners use read-only tokens and do not retain checkout credentials or
receive repository secrets. Installed-agent,
live-account, browser, and privileged certification remain explicit qualification;
portable checks exclude the existing `certification` marker. Workflow files are
authored source and participate in canonical source/archive identity.

## Why this exists

CI/CD failures are expensive when the real dependency is implicit: a graph keyword changes inheritance, a YAML indentation error changes structure, a secret appears only on one runner, or a step silently relies on files/cache/environment left behind by another step.

oh-goon moves those failures to deterministic boundaries before physical execution whenever they can be proven there.

```text
authoring source
    -> typed resolution
    -> repository baseline admission
    -> executor/isolation/resource admission
    -> RUN_ADMITTED
    -> frozen execution inputs
    -> isolated step attempts
    -> evidence-bound result
```

The key rule is not “run an Enforcer job first.” The rule is stronger:

> **Validation and capability checks that can invalidate the run are admission conditions, not pipeline steps.**

This rule applies to every execution purpose, not only strict CI/CD. Effect-free repository facts that decide whether a resolved Plan is runnable—working directories, declared paths, imported environment requirements, import applicability, required local configuration, source-layout validity at a self-certification boundary, and comparable prerequisites—must close before `RUN_ADMITTED`. Strict CI/CD then adds stronger repository-topology, typed requirement, executor/isolation, resource, and placement admission. Runtime preparation may revalidate already-admitted facts immediately before effect-start to detect drift; revalidation must never be the first place an invalid run is discovered.

This is one source of truth, not two purpose-selected validators. `FleetAdmissionPlanner` owns the base repository+Plan admissibility decision for every Fleet Plan. Strict CI/CD extends that decision with additional strict proofs; verification, inspection, maintenance, and migration do not get an admit-first path.
The base decision emits one compact repository/Plan admission receipt per selected repository, including the exact filesystem subject instance in its semantic identity and binds it into `RUN_ADMITTED`. Runtime preparation compares current repository-resolvable semantics to that exact receipt; disagreement is staleness, not permission for a second validator to choose a different answer. Retained cross-run cache references are intentionally strict-CI/CD-only because their immutable bytes are frozen/verified by strict prepared-run authority; non-CI/CD Plans reject retained cache sources during resolved-Plan validation.

A failed admission creates no partially running Fleet and no durable Run/attempt that later discovers it was never valid to start. This boundary does not pre-execute the workload: test failures, verification findings, command failures, and other outcomes produced by a validly admitted operation remain execution evidence rather than being promoted into shadow admission rules.

## Principle: no magic

> **If the user did not declare it, oh-goon does not invent it. If executable intent is ambiguous, admission fails instead of guessing.**

This applies to purpose, target scope, failure/retry control flow, dataflow, step kind, runtime acquisition, host authority, process deadlines, repository interpretation, migration, and requested runtime guarantees. oh-goon does not silently expand an omitted repository list to the fleet, inject policy from a top-level `defaults:` block, reinterpret a generic command because it starts with `git`, acquire unused extension-catalog entries, discover Docker or executor authority from host conventions, invent or truncate a process timeout, serialize a source pipeline that was concurrent, pick `origin` because it looks conventional, or keep a failed workflow admission alive for an invisible retry.

Conservative baselines are allowed only when they **reduce authority**, remain stable, are documented, and are visible in resolved-plan diagnostics. Examples are one attempt unless retry is declared, stop-on-failure for a normal step, no egress, an empty/private cache input, and serial repository execution inside an explicitly declared execution purpose. They never widen authority or replace an ambiguous user choice.

### Trainer replay is admitted data, not runtime intuition

Automatic step replay is one of the easiest places for a CI/CD engine to become surprising. oh-goon
therefore treats retry as immutable plan intent. A retry-enabled step declares the completed exit
codes that are known transient, a human-readable reason that explains why replay is safe, and every
delay between the bounded attempts. The runtime does not infer exponential backoff, add jitter, parse
logs, classify exceptions, or silently extend the schedule.

Generic outer replay is accepted only when the step has **no network egress** and executes in a fresh
attempt-private boundary. Networked generic commands are rejected because a completed failure can
have an unknowable external side effect. Cancellation, ambiguous process loss, and unknown external
effects are never Trainer-retried. Each retry decision is canonical attempt evidence, and an extra
attempt is invalid unless the immediately preceding attempt contains the matching `SCHEDULED` fact.

## Local Enforcer command

Developers inspect a Team through the **same repository-root `goon.yml`** that will execute:

```bash
uv run --locked --project backend oh-goon enforcer run /path/to/team
uv run --locked --project backend oh-goon team validate /path/to/team
```

Both are **read-only views over the canonical resolved plan and admission policy**. They are not second validators and they are not runtime stages. No local command hard-codes a Python admission profile or guesses requirements from files in the repository. If the Team requires `pyproject.toml`, `.python-version`, `uv.lock`, `Dockerfile`, Ruff/ty, etc., those facts originate in `goon.yml` and its resolved plan contract.

Machine-readable preview output is available with:

```bash
uv run --locked --project backend oh-goon enforcer run /path/to/team --json
```

Plan syntax/composition is validated through the same Python resolver used by execution:

```bash
uv run --locked --project backend oh-goon plan validate fleet-plan.yml
uv run --locked --project backend oh-goon plan check plans/
```

`plan run` does not trust those earlier commands as a lease or certificate. It resolves/admit-checks the actual requested run again.

Fleet Plan target scope is also explicit:

```bash
uv run --locked --project backend oh-goon plan run fleet-plan.yml --repository-id <id>
uv run --locked --project backend oh-goon plan run fleet-plan.yml --all-repositories
```

`plan run` is the Shared Run path and keeps explicit multi-repository scope. `--all-repositories` is an explicit operator decision, never the meaning of an empty argument list.

`plan schedule` and `plan reschedule` are different: they require exactly one `--repository-id`. A repository Schedule may not use `--all-repositories`; cross-repository execution belongs to Shared Runs. A Schedule selects one resolved Plan and does not contain executable scheduler steps.

Strict CI/CD backend authority is also explicit. `FleetExecutorPool` selects and reserves one exact executor/backend before `RUN_ADMITTED`; the admitted backend/configuration evidence is then consumed by `FleetPlanRunExecutor.prepare_strict_run()` and revalidated before launch. Strict preparation never performs a second backend search, while direct plans use the separate `prepare_direct_run()` path and cannot consume isolation evidence. Missing admitted evidence, changed configuration, or unavailable exact placement fails closed instead of selecting a currently suitable fallback.
The durable admission shape is equally explicit: `RUN_ADMITTED.execution` is either `kind: direct` or `kind: strict`. The strict variant contains one `placement` fact whose backend evidence and executor-capacity reservation were selected together before admission, plus optional placement-loss re-admission lineage. Execution revalidates and later releases that same placement; backend/capacity are never carried as sibling authority fields or paired again downstream.

Goon source scope is independent of repository target scope and has exactly two normal modes:

- **Team Goon:** resolve the exact selected Team's root `goon.yml` bytes; execution scope is exactly that one Team.
- **Shared Plan:** resolve one explicitly selected oh-goon-owned `plan.yml` once; execution scope is the explicit repository set. Target repositories' own `goon.yml` files are not read, validated, merged, inherited, replaced, modified, or used as fallback for that run. Plans select/order Goons; executable behavior remains Goon-owned.

Both modes converge only *after* source resolution on the same Fleet Plan, FleetRun, executor, Journal, and Enforcer boundaries. Source provenance remains one fact and repository execution scope remains another; a shared source is never copied into per-repository source facts.

Effectful direct maintenance is explicit too: `oh-goon run <action> <repository>` requires the repository argument; only read-only inspection commands may use a convenience current-directory default.

Normal `run:` Steps do not author `timeout_seconds`; the backend owns their command deadline. Typed capabilities may expose an explicit deadline only when that deadline is part of the capability boundary. GitLab migration preserves this rule: a normal command source timeout is representable only when it matches Oh Goon's backend command default, while typed source operations must map to their typed deadline semantics. Unsupported timeout semantics block migration instead of being silently dropped or clamped.

Strict pipelines do not receive arbitrary host-filesystem authority. `read_only_host_paths`, host `docker_socket`, and host `docker_config` are not Fleet Plan capabilities. A host Docker daemon can mount any host path the daemon can see, so exposing its socket would make a categorical repository-isolation guarantee false even if the process sandbox itself were correct. Executable requirements are derived from the resolved steps themselves; pipeline authors do not declare a second tool manifest. Executor-inventory **v6** gives the local strict backend an exact `runtime_inspector`, non-root `trusted_host_roots`, and ordered `tool_search_paths`. Queue order, drain/trust state, admitted capacity, and Fleet admission ownership are private SQLite coordination; queue tickets are owned by the exact runtime session + PID + process-start identity and cannot be adopted by another live session in the same process. Executor inventory has no runtime state-directory/lock-path fields and executor admission uses no filesystem sidecars. Before effects, Enforcer proves every required executable exists in that admitted environment; local strict execution freezes those exact observed bytes into the run toolchain. Caller `PATH` is never inherited as authority. For image-backed executors, the pinned execution image is the natural owner of installed tool versions. Bubblewrap/cgroup/restricted-egress controls remain exact inventory facts, never ambient environment discoveries. An explicitly named secret indirection such as OpenSandbox `api_key_env` remains valid because inventory names the secret dependency without serializing its value. OpenSandbox remote executors also name an absolute `cli_executable` and `runtime_attestation_file`; executable bytes and attestation bytes are part of backend configuration identity. The attestation binds the endpoint, protocol, Kata/Firecracker runtime class, and exact server-configuration SHA. A bare runtime string does not establish isolation authority, and runc/gVisor are not admitted by the deny-all remote backend.

## Repository path isolation is a trust boundary

A path being absolute or lexically below a repository is **not** enough to prove ownership. Strict CI/CD therefore uses central path types/layouts from `core.path_authority` rather than ad-hoc joins and containment checks. Repository-relative authoring has one canonical POSIX grammar; runtime workspace/output/cache paths are bound to one repository root; durable run/content paths use collision-resistant storage components; and host-to-sandbox translation uses a fixed Fleet namespace (`/workspace`, `/toolchain/bin`, `/managed/...`, private HOME/tmp/cache/config). Host source topology is not leaked into sandbox mount destinations.

Before `RUN_ADMITTED`, every selected repository is checked as a set: roots must be disjoint and non-aliasing. Each strict repository tree must have no escaping symlink, nested mount point, nested Git repository/submodule, cross-filesystem entry, special filesystem entry, or multiply-linked regular file. The proof is repeated immediately before the repository snapshot. Snapshot file access uses no-follow opens so a file path cannot silently become a followed symlink while Fleet freezes bytes. Invalid topology fails admission/execution preparation; Fleet does not repair, flatten, or guess ownership.

Cross-run retained data is repository-owned authority, not a global hash lookup. Physical bytes may deduplicate by SHA-256, but a SHA/object ID is evidence only. Consumption requires an opaque `ref-...` record owned by the current `repository_id`; references and leases are stored under repository-scoped paths. Knowing another repository's object hash or reference ID does not grant access. A repository receives a reusable reference only by publishing the bytes under its own ownership.

Strict preparation follows the same rule. `git`/`uv` are exact deployment-owned executables, acquisition runs with Fleet-owned HOME/tmp/cache/config and a minimal environment, and local extension sources must remain inside the declared catalog root. Managed runtime host paths are snapshotted before effects and remapped to Fleet-owned sandbox paths such as `/managed/000`. Managed content/run/preparation directories are owner-only on the host.

This guarantee is scoped to oh-goon's strict CI/CD authority model. The operator/root account, host kernel, and configured isolation backend remain trust roots; a malicious privileged operator can defeat any user-space path policy. For repositories that are themselves hostile rather than maintained/trusted, use a backend with the stronger requested threat model instead of relying on the local shared-kernel Bubblewrap profile.

The detailed path threat model, post-process ordering, remote-archive acceptance rules, and repository-scoped retained-content capability are documented in [`development/PATH_ISOLATION_HARDENING.md`](development/PATH_ISOLATION_HARDENING.md).

## Local League and repository CI entrypoint

The developer saves local **League folders**. oh-goon derives **Teams** by scanning Git repository roots beneath those folders; the Team list is not persisted as a second catalog. The one canonical repo-owned CI/CD entrypoint is exactly `<git-root>/goon.yml`, analogous to `.gitlab-ci.yml`. There is no recursive search, `goon.yaml` fallback, nested override, or pyproject-based inference.

League scanning is read-only. It may safely fingerprint stable root `goon.yml` bytes, but it does not resolve the pipeline, acquire runtimes, import secrets, or execute anything. Explicit `team validate` resolves that exact file through the canonical Fleet Plan resolver without execution.

Oh-Goon also supports a saved **Shared Plan `plan.yml`** stored in local configuration and applied to an explicit set of current repositories. This is a mutually exclusive Plan source, not an override layer: when a Shared Plan is selected, repository `goon.yml` files are omitted from that run and are not read by execution target selection, resolved, validated, merged, inherited, executed, or used as fallback. Repositories do not need a repository-owned `goon.yml` to participate in a Shared Run. Both sources converge on the same `FleetPlanService -> FleetRun` execution path.

## Repository target and capability requirements

Repository identity belongs to the CI/CD execution target, not to Goon authoring. Selecting a repository execution target means Oh Goon verifies managed Git-root topology and source isolation before admission. The frozen execution snapshot intentionally excludes Git metadata and revalidates only repository-source facts that are meaningful against those immutable bytes. A Goon does not repeat `git_repository` or `.git` layout checks.

`requirements:` is reserved for capability-specific repository facts that are not already known by the execution boundary. For example, a typed observer may require a real project file, while `uv:` automatically owns the locked UV-project contract and validates `pyproject.toml`, `uv.lock`, and the selected dependency group without copying package lists into YAML.

```yaml
version: 1
requirements:
- kind: file_exists
  path: schema.json
steps:
- run: uv run validate-schema schema.json
```

Requirements remain owned by the Goon that declares them. A missing imported-Goon requirement makes that import `NOT_APPLICABLE` and skips only its owned Steps; it is not promoted into a global Plan admission failure. Composite Goons may have no own requirements while child Goons own capability-specific prerequisites. Plan-owned requirements remain hard admission requirements. Repository-path requirements may name only canonical source paths that survive sanitization; generated/operational paths such as `.git`, `.venv`, `node_modules`, caches, and generated output are invalid requirement targets. Resolution preserves provenance; there is no hidden promoted requirement or shadow dependency model.

For canonical shipped-template resolution versus execution-time input transport, see
[template input resolution and transport](development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).
The Plan forwards Goon-owned input contracts; Step outputs are producer evidence, not
additional resolution sources or Plan-authored schema.

Common input validation should use the small domain types (`hostname`, `repository_path`, `git_revision`, `digest`, `choice`) instead of copied regexes. `pattern:` remains an advanced string escape hatch.

## Normal trust and resources are resolved machine policy

Omitted `execution:` resolves to local `verification`. Ordinary trusted repository checks and
agentic loops run through the normal Fleet/Step lifecycle without independent signer custody,
fs-verity, privileged enrollment or an installed local model. Provider configuration selects
the actual endpoint and model. The CLI waits for completion by default.

Strict execution is an explicit choice for untrusted code or additional guarantees:

```yaml
execution:
  purpose: cicd
  trust_repository_code: false
```

Use `oh-goon plan run ... --strict` with the configured strict executor/deployment authority.
For additional isolation around trusted code, omit `trust_repository_code` or set it to `true`.
A strict Plan cannot run through the ordinary local profile. Missing strict prerequisites fail
before workload; the command never falls back to host execution. `--standard-local` remains
an explicit spelling of the ordinary profile. Deployment integrations select strict admission
when they supply their provisioner. `--no-wait` is available only for deployed strict execution.

Within strict CI/CD, repository code is trusted by default and CPU, memory, and PID caps are unspecified.
There are no hidden static CPU/RAM/PID defaults. Omitted caps inherit the capacity available from the
selected executor for that admitted run, and the exact reserved capacity is bound into admission and
Enforcer evidence. A later run may legitimately observe different available capacity.

Portable/user-authored Plans may impose a hard cap when the operation genuinely requires one:

```yaml
execution:
  purpose: cicd
  resources:
    memory_mib: 4096
```

Omitted resource fields continue to inherit admitted executor capacity. A concrete numeric field is a
hard author cap and therefore requires matching executor/isolation capability before `RUN_ADMITTED`.
Canonical shipped Plan templates intentionally do not author fixed resource caps.

Enforcer records the resolved strict trust choice and the backend must prove the requested guarantee before execution.

## Network is explicit without low-level boilerplate

Network remains denied by default. Most networked commands use the readable step-level shorthand:

```yaml
- id: audit
  run: uv audit
  network: true
```

`network: true` compiles to canonical typed egress intent; it is not inferred from argv and it never means "whatever networking happens to work." Destination-specific hostname/CIDR policy remains available through the advanced `capabilities.egress` form. A step cannot specify both forms. See [`development/GOON_AUTHORING.md`](development/GOON_AUTHORING.md) for an inline restricted-egress example.

## Hacker Hockey vocabulary maps onto the real model

The product theme uses a hockey team to make the safety model memorable, but it does not restore a stage scheduler. **The Enforcer**, **Equipment Manager**, **Warm-Up**, **Red Team**, **Trainer**, **Coach**, **Linesman**, **Penalty Box**, **Goalie**, **Zamboni**, and **Mascot** are presentation vocabulary over canonical admission/execution/evidence facts. **The Scout** is the separate optional cross-run workload/resource observability feature and remains outside execution control. The complete copy and UI contract lives in [`HACKER_HOCKEY_THEME.md`](HACKER_HOCKEY_THEME.md).

The rule is simple: **humor may rename an explanation, never a guarantee.** If a persona status cannot be derived from canonical evidence, the UI must not invent it. Repositories/Runs Rink Board fields are projections of same-run canonical facts. Game Tape step evidence owns only timing/attempt/tool chronology, while the backend-derived Game Sheet trust receipt owns admission, runtime/tool authority, explicit cache lineage, boundary policy, release/readiness, teardown, and artifact/output digest evidence. React renders the generated receipt and does not reclassify those facts. The projection is allowlisted: it does not dump secrets, resolved values, argv, stdout/stderr payloads, or resolved network addresses. Strict Fleet Plan results carry canonical per-step teardown evidence for attempt-private roots. Zamboni renders exactly that receipt: cleanup failure is a known failed run fact and later steps stop. Release-capable attempts separately carry typed release-boundary evidence: immutable OCI publication identity or the exact Kubernetes image-state transition/observation. Goalie derives its ledger only from those facts, never by parsing workspace JSON. A run can therefore fail while still truthfully reporting a release that already crossed the boundary. The receipt does not claim retained-content deletion, external-system cleanup, signing, scanning, rollout readiness, or rollback.

### “The Enforcer” became admission

Repository/configuration validity belongs before `RUN_ADMITTED`. Every Fleet Plan purpose consumes the same repository-resolvable admission owner; verification is not allowed to defer the first answer to runtime preparation merely because it does not use strict placement. The CLI command above exposes that rule locally, but execution does not schedule a special Enforcer job.

For a strict CI/CD run, oh-goon validates **every selected repository** before admission. One invalid repository rejects the whole requested target set; valid repositories do not start early.

The exact frozen repository snapshot is validated again before strict run resources are captured, so a live-worktree change cannot invalidate the proof silently between admission and effect-start.

### “Warm-Up” became explicit preparation and typed cache dataflow

There is no magical cache stage and no automatic cache inheritance.

`plan prepare` is the explicit runtime-acquisition command for pinned built-in/extension runtimes. During a strict run, host tools, runtime roots, read-only inputs, managed runtimes, and imported host values are frozen before effects.

For the local Bubblewrap backend, that frozen runtime root also owns the mount scaffold for every later bind destination. Attempt-private `/etc/{passwd,group,nsswitch.conf,hosts}`, frozen `/toolchain/bin/<tool>` files, and `/managed/<index>` directories therefore have deterministic pre-created mountpoints before the runtime root is mounted read-only at `/`. Bubblewrap never gains permission to create paths inside the immutable runtime root during process launch; a missing mountpoint is a runtime-root construction defect and fails certification rather than being repaired by weakening the read-only root.

Python runtime closure includes the native dependencies of stdlib extension modules, not only the interpreter executable. Fleet selects the system Python and stdlib/platstdlib directories first, then inspects the `*.so` modules already inside those directories with the same trusted runtime inspector and freezes only the shared libraries and exact ABI aliases they require. A later import such as `_ssl` therefore cannot reach live host `/lib`/`/usr/lib`; if its native closure cannot be frozen exactly, preparation fails before execution.

Step cache state crosses a boundary only through a declared cache edge. Cross-run reuse uses an opaque repository-owned `ref-...` that points to immutable content; the content SHA is evidence, not authorization. There is no mutable `latest`, branch cache, ambient shared cache key, or global hash-as-capability lookup.

The shipped Python templates declare only the locked UV environment they need. Interpreter preparation and UV cache reuse are backend execution plumbing derived from the repository lock/runtime/group; authors do not create cache-prime Goons, fallback keys, warm-up Steps, or manual lock-check Steps.

### OCI image build, publication, and promotion are three different authorities

### Kubernetes image commit is not generic apply or rollout readiness

`kubernetes_set_image` is the only shipped Kubernetes mutation boundary. It targets one declared HTTPS API server, namespace, `apps/v1` Deployment, and container; the desired image must resolve to one immutable OCI digest reference. Fleet does not read kubeconfig/current context, invoke credential plugins, inherit proxy/TLS variables, run Helm, or perform generic `kubectl apply`. Host-backed bearer-token and CA values are named by the plan, validated during host-resource capture before admission, then materialized only in attempt-private curl configuration. That private root is removed synchronously and the deletion is verified; if cleanup cannot prove the root is gone, the typed step fails explicitly while any already-established remote release/readiness fact remains canonical evidence.

The executor first observes the exact Deployment, then submits an RFC 6902 JSON Patch containing `test` operations for the observed `metadata.resourceVersion`, container name, and current image before replacing the image. An API conflict/rejection is a known failed attempt. Loss of an authoritative result after the PATCH is an external-effect-unknown outcome and is not automatically replayed. Success means the Deployment spec update was committed and evidenced; it does **not** mean Pods became Ready. Rollout observation belongs to a separate typed boundary.


Strict Fleet never treats a Docker-style command line as implicit image authority. `oci_build` owns
repository Containerfile policy, base-registry egress, frozen Buildah execution, `RUN` network denial,
and local OCI archive evidence. It cannot authenticate to or publish into a destination registry.

`oci_publish` consumes exactly one explicitly published earlier workspace. Its target registry and
repository are resolved plan data; its credential fields name `from: host` sources whose values are
captured before effects but never become ordinary step imports. Attempt execution writes those secret
bytes only to private auth state. Publication is digest-only: the archive manifest is validated before
network access, Skopeo is run with TLS verification, digest preservation, zero automatic retries,
explicit single-copy concurrency, Fleet-owned temp/config paths, and a private registry config that
disables ambient mirror/drop-in rewriting,
and success publishes `OCI_DIGEST` plus the full digest-qualified `OCI_REFERENCE`.

A mutable tag such as `latest`, `beta`, or a semantic version is neither build nor publication. Tag
movement changes a registry name-to-digest mapping and therefore needs its own future promotion
contract with explicit overwrite/concurrency semantics. No current step silently creates or moves one.

### “Minimum Quality Bar” is explicit Goon intent

There is no hidden Python quality manifest. A Team that wants Ruff, ty, tests, pre-commit, lock checks, or other minimum-quality gates writes those requirements and steps in its repository `goon.yml`; a Shared Run instead selects a `plan.yml` that orders reusable Goons. In both cases the executable quality behavior remains explicit in Goon source.

### “The Linesman” became compiled capability boundaries

There is no continuously polling Linesman owner watching the pipeline from the side.

Instead, each resolved step carries explicit authority: environment imports, workspace lineage, cache lineage, logical host tools, egress, isolation threat model, and resource guarantees. Repository filesystem authority is supplied only through the central repository/run/sandbox path model; arbitrary host paths and host Docker-daemon authority are absent. Invalid combinations are rejected before execution where possible; the selected process backend then enforces the admitted boundary for the attempt.

This avoids a second runtime state machine whose observations could disagree with the actual execution owner.

## Plan/Goon composition rules

Plans select and order Goons. Plans never contain executable Steps. Atomic Goons own Steps; Composite Goons own ordered `goons:` imports. A Goon declares one form, never both. Imports retain exact `category/name` identities and written order. Inputs use unique named projection or explicit import-local `pass:`/`with:` on Plans and Composite Goons; multiple recipients must be explicit. Project-local sources select a contained catalog with `from:` and never shadow the shipped catalog. Requirements remain owned by their declaring Goon. See the [developer journey](../templates/README.md).

Ordinary verification Plans may carry derived native uv project/group environment
requirements. Other repository admission requirements remain CI/CD-only. Local
verification is explicitly selected and does not replace configured strict execution
for workloads that require its capabilities.

Reusable CI/CD behavior therefore belongs in Goons. Reusable capability bundles should normally be Composite Goons; Plans remain small intent rosters. GitLab migration writes an Atomic Goon via `goon import-gitlab`, never an executable Plan.

## Inputs, environment and secrets: no ambient export

The dataflow surface is intentionally small:

```text
inputs      immutable run parameters
env         immutable named host or literal values with an explicit source
step output direct reference to an earlier declared output
steps       fresh isolated attempts
```

Inputs and environment values are not automatically exported into every process. A Step receives only the values it explicitly imports plus the deterministic strict baseline. A later Step may reference an earlier declared Step output directly; there is no intermediate variable namespace or output-to-environment alias layer.

Secret-bearing host values are captured only when explicitly imported, are used for execution/redaction, and are not persisted as canonical evidence or dumped globally to stdout.

## Parallelism is admitted, not ambient

Repository-level parallelism is explicit and bounded. For strict CI/CD, the plan's `max_parallel` is only an author upper bound. oh-goon reserves aggregate executor capacity before `RUN_ADMITTED`, persists the admitted placement/parallelism, and never widens it later because a worker happens to look idle.

Caller-stable run IDs are single-flight before placement/admission so a retry cannot accidentally submit the same logical run twice.

## Operator placement is explicit evidence, not pipeline inference

A plan declares required guarantees, not a physical executor name. Before `RUN_ADMITTED`, the operator-owned pool may choose among executors that prove those exact guarantees using its configured priority and deterministic rendezvous/FIFO policy. The selected executor, backend, capacity, placement policy, and rank are bound into admission evidence.

That choice may affect **where** equivalent admitted work runs; it may not change **what** the plan means or weaken a requested guarantee. After admission there is no automatic relocation or downgrade. Loss of the exact admitted placement fails closed or requires explicit re-admission.

## Fail fast means “no partial execution”

The baseline inspector collects all deterministic repository problems it can find in one read-only pass. That is intentional: developers should get one useful report rather than fix one missing file only to discover the next issue on the next invocation.

The hard boundary is execution:

```text
invalid configuration or missing prerequisite
        -> diagnostic(s)
        -> admission rejected
        -> no execution

valid explicit intent
        -> immutable admission
        -> isolated attempts
        -> canonical evidence
```

No silent fallback. No partial fleet start. No compatibility path around a failed pre-live contract.

## Requirements we deliberately do not carry forward

The following ideas from the older brief should **not** be reintroduced:

- duplicate step IDs where the first occurrence wins;
- generic runtime URL includes;
- a global variable/secret dump enabled by default;
- ambient variable, artifact, workspace, or cache inheritance;
- a mutable settings panel that can silently change admitted execution semantics mid-run;
- a separate Enforcer/Linesman scheduler or event model;
- fallback from a requested isolation/resource guarantee to a weaker backend;
- partial strict CI/CD execution when any selected repository fails baseline admission;
- compatibility shims for the old `uv-fleet` / `uv_fleet` naming;
- arbitrary host-path or host-Docker capabilities in strict repository pipelines;
- content hashes/object IDs as cross-repository read authority.

The public product/CLI/package name is **oh-goon** / `oh-goon` / `oh_goon`.


## Kubernetes rollout verification is an explicit read-only step

`kubernetes_set_image` and rollout health are separate contracts. A release plan that wants readiness
proof must add `kubernetes_rollout_verify` after the mutation and bind its expected generation and image
to that exact earlier step's published outputs. Resolution rejects another source step, another target, or
another credential authority before admission.

The verifier issues only bounded GET observations against the declared Deployment. It has an explicit
overall timeout, poll interval, request timeout, zero transport retries, and no outer step retries. READY
requires the exact expected generation/image plus observedGeneration and replica readiness. Superseded
state, ProgressDeadlineExceeded, and timeout fail closed and are recorded as canonical attempt evidence.
No kubeconfig/current-context discovery, hidden watcher, workspace JSON parsing, or release replay is
permitted.


## Frozen host-tool authority

For strict CI/CD, a host tool is admitted by logical name through one operator-owned `tool_search_paths` directory entry and executed from one run-owned immutable snapshot. Distribution-owned executable symlinks such as NVM `npm`/`npx` remain valid name mappings even when their dereferenced package entrypoint lives outside the `bin/` directory; pipeline authoring still cannot request that target path directly. Enforcer preflight verifies that every required logical name exists in deployment path authority. Run preparation freezes the dereferenced executable bytes plus required runtime support and records the exact step consumers. Host-origin attempt evidence must match that frozen manifest exactly. There is no ambient PATH lookup, version fallback, arbitrary absolute host-path selection, or runtime substitution after admission.

### Shared-Plan Lineups and preview

A Shared Plan may use either an explicit one-off Team selection or one saved local Lineup. The selectors are mutually exclusive and both resolve to explicit current repository IDs before execution. A Lineup remembers Team roots only; it never merges repository `goon.yml` files, queues work, or stores run state. `shared-plan preview` resolves the selected Shared Plan `plan.yml` once and checks every selected Team through the canonical read-only FleetPlan/Enforcer preview boundary. Preview returns the canonical execution-lock digest for the reviewed source plus inputs; Game Start still resolves and admits again through the normal FleetRun path and uses that digest only as a fail-closed equality fence against semantic drift. It is not a reusable admission receipt.
