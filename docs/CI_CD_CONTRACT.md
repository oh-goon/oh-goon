# oh-goon CI/CD contract 🧹

> Old hacky code, gone. If it doesn't validate, it doesn't run.

This document is the short product-level contract for local CI/CD execution. The detailed mechanics live in [`development/FLEET_PLANS.md`](development/FLEET_PLANS.md), while [Architecture](ARCHITECTURE.md) explains the current ownership boundaries.

## Repository checks on GitHub

Ordinary updates use [relevant verification](../.github/workflows/ci.yml).
Hashmarks supplies source-qualified direct and indirect verifier nominations;
Oh-Goon admits the affected fast tests, bounded real proofs and applicable
static/contracts. The existing native batch owner collects and executes that
flat membership. It retains source/configuration/environment identity and exact
results. A nomination does not establish whole-owner coverage, and bounded
producer qualifiers remain visible.

The same admission is available locally through `make check` or `make test`.
Use a focused owner test while editing; these front doors do not run unrelated
complete suites. `VERIFY_BASE=<commit> make check` selects an explicit comparison.
`make merge-check` produces candidate-bound selected proof for independent gate
validation. `make broad-diagnostic`, complete owner targets and `make release-check`
remain explicit broad diagnostics/daily/release actions, not hidden prerequisites
of ordinary changes.

A ready PR or merge group checks its effective checkout SHA against its actual
base. The PR head is recorded separately where it differs. Native Git accounts
for additions, deletions and both sides of renames; local feedback also includes
untracked source. Unknown consumer scope remains `SELECTION_UNVERIFIED`; it never
passes as an empty selection or silently launches the daily corpus. Governing
instructions, workflow/result policy and selector changes require the named
**verification-policy protection** obligation. Shared fixture or dependency
uncertainty cannot be excused by that generic policy proof.

Merge-mode plan admission retains diagnostic JSON but refuses unresolved scope
or missing required behavioral proof before publishing toolchain-selection outputs
or preparing frontend/native-test capabilities. Focused plan inspection remains
available for incomplete selections. Execution applies the same selection guard
after checking the retained plan's current source; early refusal does not establish
verification or relax the independent trusted-base gate.

`daily` marks expensive ordinary journeys assigned to daily execution. It is
separate from `slow` (exhaustive variants), `process`, `scale` and `certification`.
Normal selected pytest excludes `certification`, `slow` and `daily`; a necessary
critical/reproducer proof must be explicitly admitted rather than omitted by
association. Changed daily test logic needs meaningful bounded execution; missing
precision blocks qualification instead of expanding every parameter automatically.
All retained portable instances, including unrelated fast tests, remain in the
unfiltered daily portfolio.

The candidate job emits **MERGE_PROOF_READY** only after its selected checks.
The required **merge-gate** separately checks out the immutable base controller,
inspects the candidate as data, and validates the bounded same-run proof artifact.
It never executes or imports candidate code. Missing, skipped, failed, cancelled,
stale or mismatched proof cannot qualify. The base control also rejects missing
owners and changed tests without meaningful execution. Absence of reviewed base
controls is **CUTOVER_UNVERIFIED**. Draft feedback has the distinct nonrequired
name **draft-feedback** and means **FOCUSED / NOT_FULLY_VERIFIED**.

Only needed toolchains are materialized with native committed locks. Python-only
work does not install/build the frontend. Node/npm is prepared only for admitted
frontend obligations. Python verification consumes the committed `dev`, `quality`
and `build` groups, matching `make backend-install`; native wheel tests reuse
that prepared interpreter and build backend. Native uv/npm download caches are
distinct from PASS receipts; virtual environments, mutable databases and test outcomes are not
shared as source-independent proof. Selected Python tests retain the original
hosted native runtime view and its owned SDK-overlay cleanup through
`ci_native_runtime.sh`; static/docs checks do not mount it. CPU budgets and
process cleanup retain their existing native owners.

PR, merge-group and main-health concurrency are separate. Superseded PR work is
cancelled; main pushes execute only distinct installed-source/CLI smoke. The
complete Python CI example is a daily reusable/manual workflow, not an additional
complete journey on every ordinary update. Package/build smoke is admitted when
its actual distribution boundary changes; ordinary docs do not assemble a release.

### Effective protection and cutover

Install reviewed base controls and establish daily coverage first. Exercise the
fast route and its decisive negative scenarios against an exact candidate. Then
configure the existing main ruleset and any legacy protection to require
**merge-gate** from GitHub Actions, retain review/bypass restrictions and require
an up-to-date branch or verified merge queue. Prove that a failing candidate is
blocked. Remove obsolete required check names only after replacement enforcement
is established, without an unprotected interval. Do not retain duplicate complete
and selective gates indefinitely.

Workflow YAML alone does not enforce branch rules. An unavailable ruleset or
legacy-protection read means effective enforcement remains unverified. Keep the
replacement PR in draft until reviewed source, daily execution and the required
administrator cutover are ready. A green optional workflow is not merge protection.
A bounded manual replay uses **Relevant verification → Run workflow** with an
immutable base SHA; it does not alter default-branch settings.

The normal required gate targets p95 ≤300 seconds and aims for p50 ≤120 seconds,
including queue, setup, selection, execution and aggregation. Local warm focused
feedback aims for p50 ≤10 seconds and p95 ≤30 seconds. These are measured goals,
not permission to shorten timeouts or truncate membership. Timing is provisional
until representative ordinary observations exist; 30 samples are an initial
empirical cohort, not a prerequisite for safe functional cutover. Report cold/warm
setup, failures, cancellation and fallback separately using existing timestamps
and receipts.

## Use Oh-Goon in another repository's CI

The [Python CI walkthrough](../examples/python-ci/README.md) supplies a small
Plan, two project-local Goons, GitHub Actions and GitLab CI configurations. It
uses the existing Orders application for native lint, tests and wheel building.
The hosted platform owns triggers and job scheduling; Oh-Goon's public CLI owns
locking, admission, waited execution and Run reporting. Source selection pins
the real application's Git commit, while native uv owns dependency freshness
and build behavior. No empty target repository or alternate runner is needed.

The original content-store collector retains the declared wheel and native
Ruff/JUnit reports needed for later diagnostics. Public artifact export verifies
their bytes before the platform uploads the promoted files and portable JSON
readbacks. Failed work retains its failed outcome and
skips later Steps. A rejected invocation with no submitted Run has no execution
receipt to collect. The CLI handles SIGINT/SIGTERM through its existing Run
cancellation service; platform force-kill and artifact-upload availability
remain separate qualification boundaries. The reference GitLab configuration
requires a Linux shell runner. See the walkthrough for exact local and hosted
proof and the remaining unqualified boundaries.

## Fast feedback and complete release verification

The development objective is shorter **verified issue-to-merge time**: select an
open issue, implement it, get useful feedback, create or update its PR, pass the
required checks, merge, close the satisfied issue, and proceed to the next
authorized issue. CI and local testing are part of that workflow. Test count and
coverage percentages are diagnostics; success means retaining meaningful defect
detection while reducing avoidable waiting and repeated work.

Use focused tests while editing and the admitted fast merge proof for a coherent
candidate. Complete unrelated owners are daily work. Relevance and cadence are
separate: retain the smallest real applicable boundary proof before merge and
leave exhaustive variants daily unless a specific critical obligation or defect
reproducer requires them now. Neither touching an owner nor editing a slow case
automatically selects its full campaign.

Materialize only needed native toolchains and reuse valid dependency caches.
Keep mutable state isolated and native result/resource authority unchanged.
Record queue, selection, setup, execution, failure and aggregation through
existing GitHub timestamps and receipts. Report normal/scoped/fallback routes
and cold/warm conditions honestly; a timing goal cannot truncate admitted tests.

`make release-check` runs the complete portable suite, including `slow`, through
the same existing check owners. Backend unit/dependency-complete, backend
integration, cross-owner integration and tooling membership remain disjoint.
Privileged custody, live accounts and installed-model `certification` still need
explicit qualification and are never silently admitted by a release command.

Local `make build-release` performs this complete check before artifact assembly.
The [complete release workflow](../.github/workflows/release-validation.yml) also
runs the same canonical owners daily and on explicit manual dispatch. Its
`release` mode qualifies the selected immutable candidate before publication;
the former published-release trigger is removed. Portable success does not
replace the separately required APP/native/browser/host qualification or the
release owner's final source/artifact promotion checks. An older nightly SHA
cannot qualify a newer release. Existing exact-candidate evidence may be reused
only when the release owner already admits that reuse.
Ordinary PRs use selected proof and its independent trusted-base `merge-gate`.
Package smoke runs only for an admitted distribution-boundary change; routine
updates do not assemble release artifacts or claim APP certification.

## Daily full regression

The full workflow runs at **02:23 UTC every day** (03:23 Swedish winter time,
04:23 summer time), including days when main has not changed. PR updates and
main pushes do not trigger it. GitHub's scheduler is best effort; a cron entry
does not establish that a campaign actually ran.

The campaign freezes one main commit before work. Every owner checks out that
same SHA. `make release-check-plan` projects the existing Make owner's complete
portfolio, and the workflow consumes it with `fail-fast: false` and at most one
owner runner. Local `make release-check` invokes the same owners with native
Make `-k`, so a failed quality or integration owner does not suppress independent
owners. There is no change filter, fast/daily marker exclusion, or reusable test
PASS in this campaign. The marker is `not certification`: both `slow` exhaustive
variants and expensive ordinary `daily` journeys remain included. Dependency
downloads may use native uv/npm caches; environments and project code are
materialized for the current checkout.

The portable environment is explicitly **Ubuntu 26.04, Python 3.12, Node
24.19.0 and uv 0.12.5**, with two CPUs per native test scope. This campaign does
not claim other OS/Python variants, installed models, privileged custody or
browser/APP qualification. The latter remain separately selected obligations.
The Orders project's native pytest configuration deselects its optional
`airflow` comparison: the separate Airflow dependency group is not selected by
the portable application-test owner. Ordinary project directories are
discovered without enumerating individual filenames.
The full owner set covers backend unit/dependency-complete tests, backend and
cross-owner integrations, repository tooling, shipped Python projects,
frontend checks, architecture, generated freshness and lock validation. Native
discovery adds new portable tests automatically; no duplicate test-file list is
maintained in the workflow.

Each owner retains bounded native results outside the source checkout. The final
join requires the exact SHA, UTC slot, Actions run/attempt and complete owner
partition. Missing, cancelled, timed-out, unexpectedly skipped/xfail or foreign
evidence cannot become a qualifying green. A failing native test campaign can
have complete coverage; setup failure or an unfinished owner is `INCOMPLETE`.
Native `-ra` output retains non-PASS reasons. The batch owner's original member
and discovery receipts remain the result authority.

One daily concurrency domain never cancels an active campaign. Manual catch-up
uses the same UTC slot and the first attempt's frozen source. When a slot
already has an attempt, use **Re-run jobs** on that Actions run rather than
creating a new campaign to overwrite a red result. Release qualification has a
separate concurrency domain. A queued daily run can still be delayed by account
capacity; separate groups do not reserve runners. The initial one-owner ceiling
limits contention. PR latency during a real overlap remains a live qualification
obligation, not a claim made by this configuration.

Run a missed slot through Actions → **Daily and release verification** →
**Run workflow**, mode `daily`, with its UTC `YYYY-MM-DD` slot. Leave `ref` blank
to freeze current main when there was no prior attempt. For release
qualification, select mode `release` and an exact commit SHA; no publication
occurs. The command-line equivalent is:

```sh
gh workflow run release-validation.yml -f mode=daily -f slot=2026-10-30
gh run rerun RUN_ID
uv run --offline --frozen --no-sync --project backend oh-goon-daily-regression health --repository oh-goon/oh-goon
```

The health command reads existing Actions run metadata and bounded result
artifacts. It reports the expected daily slot, latest attempt, latest complete
result and last green separately. A fresh completed red is **fresh and failing**;
an in-progress or incomplete attempt does not refresh completion. A missed slot
is visible immediately; more than 36 hours without a complete result is the
outer stale alarm. Missing/expired artifacts remain unknown. No second
scheduler, monitoring database or fallback cron is introduced.

The repository maintainer **@Hugloss** owns the daily Actions notification and
next-working-day triage, including checking the expected slot from outside the
workflow. A workflow cannot detect its own failure to start. Preserve the failed
SHA and smallest reproducer; classify product, test, infrastructure or missing
capability failures. Confirmed product defects hold relevant releases and add
their reproducer to affected future changes. An unrelated failure does not make
every PR wait for full regression. If a campaign exceeds its slot, preserve the
active attempt, report overdue coverage and repair runtime/capacity; repeatedly
cancelling it would prevent full completion. Owner jobs have a finite two-hour
limit, and every uncompleted owner remains explicit in the final report.

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

Trusted-local verification can retain explicitly declared artifact files from a
captured package/application's owned attempt workspace. The existing Run,
collector and content-store owners preserve repository scope, settled-process
collection and byte identity; this capability issues no strict executor or
isolation receipts. Ordinary direct target-workspace artifact publication,
retained cache inputs and automatic retries keep their stronger requirements.
The default Iris Plan uses this verification route; selecting `purpose: cicd`
still requires strict admission. See [artifact retention and export](development/TEMPLATE_ARTIFACT_RETENTION.md).

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

`make strict-executor-setup` prepares native systemd delegation only. It does not
enroll Runs or establish independent kernel custody. The local cgroup effect
owner currently refuses all custody-dependent effects because it has no
provisioned independent parent owner; a successful delegation setup cannot
override that refusal. Strict Plans require their original deployment authority
and remain unexecuted when that authority cannot be established.

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

Normal CLI setup selects one versioned `run.yml` and exact registered repository path:

```bash
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

The compiler and typed binder retain source/contract authority. `plan check`
and `plan diff` remain source-authoring diagnostics. Execution verifies the
selected identity and performs fresh admission; an earlier inspection is not
a lease or certificate. Shared Plan and Schedule UI/service APIs retain their
own explicit reviewed target and source identities. The config cannot select
an implicit fleet, define scheduler Steps, or grant stored permission.

Strict CI/CD backend authority is also explicit. `FleetExecutorPool` selects and reserves one exact executor/backend before `RUN_ADMITTED`; the admitted backend/configuration evidence is then consumed by `FleetPlanRunExecutor.prepare_strict_run()` and revalidated before launch. Strict preparation never performs a second backend search, while direct plans use the separate `prepare_direct_run()` path and cannot consume isolation evidence. Missing admitted evidence, changed configuration, or unavailable exact placement fails closed instead of selecting a currently suitable fallback.
The durable admission shape is equally explicit: `RUN_ADMITTED.execution` is either `kind: direct` or `kind: strict`. The strict variant contains one `placement` fact whose backend evidence and executor-capacity reservation were selected together before admission, plus optional placement-loss re-admission lineage. Execution revalidates and later releases that same placement; backend/capacity are never carried as sibling authority fields or paired again downstream.

Goon source scope is independent of repository target scope. The browser has two normal modes:

- **Team Goon:** resolve the exact selected Team's root `goon.yml` bytes; execution scope is exactly that one Team.
- **Shared Plan:** resolve one explicitly selected oh-goon-owned `plan.yml` once; execution scope is the explicit repository set. Target repositories' own `goon.yml` files are not read, validated, merged, inherited, replaced, modified, or used as fallback for that run. Plans select/order Goons; executable behavior remains Goon-owned.

The CLI also selects exact catalog, contained file or pinned Git Plans through
[run configuration](development/TEMPLATE_INPUT_INSPECTION.md). A `git-plan`
names one authoring root and exact Plan; native Git acquisition and `goon.lock`
preserve its commit and contained local imports. Package-owned Python Goons
carry complete captured projects independently of their target repositories;
native uv installs the selected base/group dependencies in owned environments
and relative outputs use writable attempt state. See the [Python project contract](architecture/ADR_PYTHON_GOON_PROJECTS.md).

All source modes converge only *after* source resolution on the same Fleet Plan, FleetRun, executor, Journal, and Enforcer boundaries. Source provenance remains one fact and repository execution scope remains another; a shared source is never copied into per-repository source facts.

Effectful direct maintenance is explicit too: `oh-goon operation <action> <repository>` requires the repository argument; only read-only inspection commands may use a convenience current-directory default.

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
requirements:
- kind: file_exists
  path: schema.json
steps:
- run: uv run validate-schema schema.json
```

Requirements remain owned by the Goon that declares them. A missing imported-Goon requirement makes that import `NOT_APPLICABLE` and skips only its owned Steps; it is not promoted into a global Plan admission failure. Composite Goons may have no own requirements while child Goons own capability-specific prerequisites. A repository-root Atomic Goon's requirements remain hard admission requirements; Plans do not author requirements. Repository-path requirements may name only canonical source paths that survive sanitization; generated/operational paths such as `.git`, `.venv`, `node_modules`, caches, and generated output are invalid requirement targets. Resolution preserves provenance; there is no hidden promoted requirement or shadow dependency model.

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

Use the original strict/deployment owner with its configured executor authority.
Normal run selection cannot manufacture deployment custody or bypass strict admission.
For additional isolation around trusted code, omit `trust_repository_code` or set it to `true`.
A strict Plan cannot run through the ordinary local profile. Missing strict prerequisites fail
before workload; the command never falls back to host execution. Deployment integrations select strict admission when they supply their original
provisioner; their owner retains asynchronous execution semantics.

Within strict CI/CD, repository code is trusted by default and CPU, memory, and PID caps are unspecified.
With explicit `trust_repository_code: false`, native uv dependency installation
and build backends execute through the original strict isolated Step owner,
including package-owned `runtime: python` and target-project uv workloads.
Host preparation selects the exact uv/system Python authority without executing
project code. Package source remains read-only, while each attempt uses a verified
private build copy and non-editable environment in its writable cache. Preparation
uses the same selected environment, network and resource authority as the Step.
Untrusted attempts start with an empty owned cache; host caches and ambient
credentials are not transported. The current remote untrusted provider remains
VM-only with deny-all networking. Such workloads need dependencies contained in
the captured project or supplied by native uv, including its bundled backend
where applicable. Missing isolation prerequisites refuse before a build; missing
dependencies produce a native uv failure. There is no host-build fallback.
Ordinary trusted execution keeps its existing preparation behavior. Successful
installation alone does not establish APP qualification.
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

The existing runtime-preparation service owns acquisition of pinned built-in/extension runtimes. During a strict run, host tools, runtime roots, read-only inputs, managed runtimes, and imported host values are frozen before effects.

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

A Shared Plan may use either an explicit one-off Team selection or one saved local Lineup. The selectors are mutually exclusive and both resolve to explicit current repository IDs before execution. A Lineup remembers Team roots only; it never merges repository `goon.yml` files, queues work, or stores run state. The Shared Plan preview service resolves the selected Shared Plan `plan.yml` once and checks every selected Team through the canonical read-only FleetPlan/Enforcer preview boundary. Preview returns the canonical execution-lock digest for the reviewed source plus inputs; Game Start still resolves and admits again through the normal FleetRun path and uses that digest only as a fail-closed equality fence against semantic drift. It is not a reusable admission receipt.
