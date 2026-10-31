# Run configuration and inspection

Normal CLI execution selects one explicit `run.yml`. It chooses an existing
Plan or repository Goon, registered targets, scalar caller values, and relevant
agent or bounded-repair selections. It does not define Steps, permissions,
provider connections, native model settings, or a second workflow language.

This single-file entrypoint applies to local CLI catalog/file/repository
invocations. The browser's persisted Team and Shared Plan APIs retain their
distinct source-revision, repository-instance and reviewed-target authority.
They converge on the same Fleet execution lifecycle. Shared Plans expose the
same typed Agent selection and bounded review-repair budget groups through
their backend contract. The browser submits those selections with the saved
Plan revision, reviewed execution digest and exact repository instances.
Shared Agent execution selects one Team and explicit absolute repository/Skill
roots on the server; CLI `run.yml` keeps its Skill root relative to its workflow
directory. Provider/native profiles retain their existing configuration owner.
Repair requires fresh explicit authorization for each submission. Recorded
campaign results reopen through the original Journal. Initial capture can
expire or be cancelled before intent is recorded; that invocation result has
no Journal revision and grants no durable completion claim. A readback never
resumes execution.

Use [CLI-only setup](../../README.md#cli-first--no-frontend-setup):
`make backend-install` prepares the source CLI without building the frontend.
Invoke it from the Oh-Goon checkout with
`uv run --offline --frozen --no-sync --project backend oh-goon` in place of
`oh-goon` below. When working from your workload directory, replace `--project backend`
with the absolute `--project /path/to/oh-goon/backend`. Built releases use the
installed command directly.

```sh
oh-goon league add-folder /absolute/path/to/repository
oh-goon templates init python/quality --kind plan --output ./workflow --repository /absolute/path/to/repository
```

`--kind` is required because a Plan and a Goon may share the same catalog key.
For a single Goon, use `oh-goon templates init python/test --kind goon --output ./workflow --repository /absolute/path/to/repository`.
A Plan selection uses the catalog directly; a Goon selection creates a minimal
`plan.yml` importing that exact Goon and selects the file from `run.yml`. Neither
mode copies the selected Goon implementation or invents another source owner.

The initializer resolves `--repository` through the existing League registry
and writes its canonical identity. No scan or manual repository-ID copying is
needed. Keep that generated target and edit only the inputs you need. For example,
these values select the `backend` project and ty:

```yaml
inputs:
  project: backend
  dependency_group: dev
  typechecker: ty
```

Omitting `--repository` deliberately leaves a target placeholder for later
selection. An unregistered explicit path refuses before publishing the workflow. See the
[template first-run guide](../../templates/README.md) for headless registration.

Repeat `--repository PATH` for a context-free catalog, file or Git Plan over
several actual applications. The generated `target.repository_ids` contains an
explicit unique set of at least two registered identities. It is mutually
exclusive with the existing singular `target.repository_id`; a single path
keeps that original form. The same Plan/inputs execute through one Fleet Run
and its existing target scheduler. Every requested member must resolve;
duplicates or unavailable members refuse before application work. Repository
Goons, Agent and repair sources require one target. See
[the parallel walkthrough](../../templates/README.md#one-plan-over-several-applications).

The Python values above illustrate this repository's layout. Native uv, Ruff,
Pytest and the selected type checker retain their project configuration and
results. The shortest execution path after editing is:

```sh
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

Inspection and validation are optional diagnostics; lock and run still use the
canonical validation owners. To see the selected inputs or check values separately:

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config inspect ./workflow/run.yml --json
oh-goon config validate ./workflow/run.yml
```

Generation refuses an existing destination. It does not overwrite a project or
copy an unchanged shipped Plan. Inspection and validation do not start a Run,
write a lock, fetch remote Goon/Plan or Skill sources by default, contact a model, read native private config,
or grant permission. Locking and execution use the original resolver and
Fleet lifecycle. A completed validation is not an executed check or APP PASS.

For a new Git selection, `oh-goon config inspect ./workflow/run.yml --acquire`
explicitly permits the existing Git acquisition owner to capture its source
before required input values are known. It does not write `goon.lock`, install
the project or start execution. JSON and human inspection report requested
origin/ref, resolved commit, stable logical Plan/Goon paths, YAML-only hashes,
complete Python package identity, selected dependency groups and the limits of
the evidence. A successful contract inspection does not establish installation
or execution. Default inspection retains its no-network behavior and uses
existing exact pins.

## Limit a local Run

For a trusted-local, waited CLI Run on Linux, add an optional budget to
`run.yml`:

```yaml
resources:
  cpu_millis: 2000
  memory_mib: 2048
  max_processes: 128
```

The example caps the aggregate at two CPUs of execution time, 2 GiB of memory
and 128 native tasks. The memory limit disables swap for this Run. All selected
repositories share that budget, including source acquisition, project setup
and parallel Steps. Increasing repository concurrency does not multiply the
budget. CPU quotas control available CPU time; they do not pin execution to
particular cores. Native task accounting includes threads.

The scope contains the executing CLI and its descendants. A bounded launcher
outside it captures the invocation, streams output and records native
settlement; it does not acquire sources or execute the workflow. Its overhead,
the systemd user manager and other applications are outside this Run budget.

Omit individual fields to inherit their available capacity. Omitting
`resources` retains ordinary execution without adding a product default.
An empty mapping is invalid. Accepted ranges are 100–64000 CPU milliseconds,
64–262144 MiB and 16–4096 tasks.

This selection requires the local systemd user manager and cgroup v2 with the
requested controllers. Before acquiring executable sources or starting the
runtime, the CLI starts the same invocation in one native scope and verifies
its actual limits. Existing private caller limits must remain enforced; a
scope migration that would abandon one refuses before application work. Follow
the reported prerequisite or run in an explicitly selected compatible context.
There is no uncapped fallback.

The budget applies to `run`, which waits for completion. Background execution,
Schedules, Agent/repair workflows and independently isolated strict, remote or
deployment execution do not yet accept this selection. A Plan whose resolved
execution mode is incompatible refuses before application effects. Locking
and inspection remain their existing separate commands.

To run it again, use the same `oh-goon run ./workflow/run.yml` command. History
reruns refuse a budgeted Run until they can create and verify a fresh native
envelope; they cannot silently repeat it without its limits.

Run and Game Tape evidence retain the admitted native scope, requested limits
and observed kernel ceilings. Final native accounting and cleanup are separate
from the workload result. Unavailable accounting remains `INCOMPLETE`; it does
not turn a failed workload into success or change a successful Run's recorded
outcome. See [observability ownership](../OBSERVABILITY.md#resource-analytics).

## Source and value owners

Choose exactly one source:

| `source.kind` | Required selection | Meaning |
| --- | --- | --- |
| `catalog-plan` | `name: category/name` | Exact shipped Plan; no copied Plan required. |
| `file-plan` | `path: ./plan.yml` | Exact project Plan contained below the selected workflow root. |
| `repository-goon` | No `name` or `path` | Exact registered target's root `goon.yml`. |
| `git-plan` | `git`, `ref`, `path`, `plan` | Exact Plan below an explicitly selected Git authoring root. |

Mixed kinds, unknown keys or versions, duplicate YAML keys, unsafe paths,
missing sources and unknown targets refuse before effects. Selection never
searches another source or merges repository YAML with a Plan. Local Plan
imports use the normal explicit `from: ./goons` source grammar.

A complete Git example selects its authoring root and Plan separately:

```yaml
version: 1
source:
  kind: git-plan
  git: https://github.com/your-org/workflows.git
  ref: main
  path: examples/orders
  plan: plan.yml
target: {repository_id: REPOSITORY_ID}
```

`path` is the authoring root inside that Git revision; `plan` is an exact
contained path relative to it. The Plan's local `from:` imports remain inside
that selected root and revision. Selecting a single remote Goon instead uses
an explicit Git `from:` mapping in the local Plan; see
[Goon authoring](GOON_AUTHORING.md#project-owned-goons-and-caller-overrides).
Neither selection searches for a nearby Plan or shadows shipped imports.

`lock` explicitly acquires source and records its exact commit in `goon.lock`.
Ordinary lock regeneration and runs keep the pin; a missing cache may recover
that same commit. `lock run.yml --update-goons` explicitly refreshes Goon/Plan
selectors independently of `--update-applications` and `--update-skills`.
Inspection and validation use cached source only; unavailable source reports acquisition is required without
network effects. Acquisition does not run project code or install dependencies.
Explicit `config inspect --acquire` also permits acquisition for contract
discovery before a successful lock; installation and execution remain unproven.

`inputs` supplies scalar values only. The imported Goon and canonical binder
own names, types, defaults and constraints. An omitted value may use its
owner's default; an explicit invalid value fails. Ambiguous shared inputs need
explicit `pass:` recipients or renamed `with:` bindings in the Plan/Goon.
Browser fields display declared defaults without submitting them as caller
inputs. **Value source** distinguishes omission, an explicit value equal to the
default, and explicit null. Clearing a text field supplies an explicit empty
string; the existing binder decides whether that value or null is valid for
the selected input. Preview `explicit_inputs` retains exactly the supplied map.
The config cannot contain copied schemas, nested executor policy, credentials,
secret interpolation, `steps`, `goons` or native `modelOverrides`.

Optional `agent` and `repair` groups select existing invocation facts only.
Use them only for workflows that support those selections. See
[Agent Skills](../AGENT_SKILLS.md) and [Agent providers](../AGENT_PROVIDERS.md).
`.goonskills.yml` alone owns Skill source/ref/directories; `goon.lock` owns
frozen Skill commits. Provider catalogs own connection/profile definitions.
For the shipped review-repair workflow, initialization copies its existing
pinned registry and local adjudication/proposal Skill companions. Custom review
Skill resources remain caller-owned; inspection identifies remaining choices.
Pi, OpenCode and Codex own their native authentication, context windows,
compaction, reasoning, tools and private settings.

An `agent` selection requires `acceptance`, an absolute approved
`repository_root`, and a contained relative `skill_root`. Model-backed Steps require
either native `implementation` plus `profile`, or managed inference `model_profile`;
a native selection cannot also set a managed model profile. Recipient-free,
read-only `local-search` Steps use only the source roots and acceptance, without a
model selection or provider request. The authored source graph rejects a missing
model selection for other harnesses before capture or execution. `repair` requires all four
explicit limits: `review_passes`, `write_roots`, `timeout_seconds`, and
`step_timeout_seconds`. Its Run also requires fresh `--authorize-repair` consent.

A stored config is never fresh write consent. The original admission,
acceptance, isolated execution and repair-limit owners still decide whether
an effect may start. Changing source, target, input values, Skill selection,
model/profile, roots or budgets requires a matching fresh lock. Missing,
unsupported or stale authority refuses; there is no unlocked fallback.

## Locking and actionable refusals

Run `oh-goon lock ./workflow/run.yml` after reviewing a change to `run.yml`,
Plan execution policy, ordered imports, Goon behavior or requirements, or captured
application code, metadata, native lock or resources. Target-project dependencies
remain native uv authority; `goon.lock` does not replace `uv.lock` or prove that
dependencies are installed. Ordinary relocking preserves recorded Git Plan/Goon,
application and Skill commits. Select `--update-goons`, `--update-applications`
or `--update-skills` only when you intend to refresh that source class.

Lock refusals explain the boundary the existing owner could establish:

- A missing lock requires the explicit lock command before running.
- An unreadable, malformed or integrity-invalid lock needs a readable generated
  record with trustworthy pins. Restore it from version control where available
  before relocking. Unsupported formats require a compatible Oh-Goon version or
  an explicitly reviewed regeneration.
- A stale lock identifies changed Plan authority, Goon execution or bound inputs,
  caller files, Skill sources, or Git Plan/Goon/application pins and directs you
  to review that change before relocking.

These diagnostics use existing digest comparisons. They cannot recover an old
input value or identify an individual changed source file from a digest. A
numbered Goon occurrence refers to its position in the ordered execution list;
repeated imports remain distinct. The diagnostic reports bounded occurrence
numbers when available and does not expose caller values, private paths or raw
source names. Read the selected contract with `config inspect` when you need
input definitions. A refusal never refreshes pins or repairs a lock implicitly.

## One human and JSON report

`config inspect` renders the same local captured selection as `--json`.
Its report is evidence at the inspected level: source selection, typed input
contracts and their Goon owners, selected groups, and unresolved conditions.
An unprobed provider or native executable remains unknown. Contract inspection,
input validation, frozen identity, admission and actual completion are separate
facts. Publication, deployment, scanner success and model availability require
their original runtime evidence.

The resolver's `inspect_inputs`, `inspect_input_interface_with_source` and input
routing projections remain the definition and provenance owners. They use the
same canonical analyzed source graph as execution. Owner paths may be empty for
a root-owned input or contain multiple explicit child recipients. Ordered
repeated imports and aliases keep their own occurrence identities. Deferred
output/workspace references identify their real producer; their value exists
only after that producer completes.

Reports must not show credential values, native private config, prompt bodies,
source text, external error bodies or guessed readiness. A root-file digest is
source provenance, not a complete closure proof or an execution receipt.
Configuration evidence retained by Game Tape comes from the admitted target;
readback never reconstructs historical settings from current source.

Free-form string values/defaults and derived private paths stay redacted.
Step details show available authored declarations; the original resolver owns
actual membership and order. When input resolution reaches an environment
source, inspection leaves it unobserved instead of treating it as absent or
choosing a fallback. The report remains `contract-inspected` and validation
returns an unverified result. Explicit lock/run use the original environment
authority and can still reject the actual selected value.

## Read recorded results

`run` waits for completion and reports a nonzero exit code when the declared
work fails. Its human completion report includes failed Steps and their recorded
exit codes, declared artifact paths and evidence, and commands for that Run:

```sh
oh-goon plan status RUN_ID
oh-goon plan steps RUN_ID
oh-goon plan outputs RUN_ID
oh-goon plan handoff RUN_ID
```

These views reopen the original durable Run and Game Tape after the execution
process exits. `plan outputs` reports hashes, byte counts and retained references
when present; it does not download artifact bytes. An `evidence` artifact does
not promise retained file bytes. Read `oh-goon plan logs RUN_ID` while transient
logs are still available. Historical evidence stays separate from current
workspace files and current source. See the [artifact retention contract](TEMPLATE_ARTIFACT_RETENTION.md).

## Clean break and evidence limits

The former Plan-path/`--input`/`--inputs-file` and repeated agent/root/repair flag
assembly is removed from normal public execution. Old schemas and unsupported
commands fail; no adapter, migration or alternate success path is provided.
`plan check` and `plan diff` remain source-authoring diagnostics. Internal
certification and release use their owner-controlled Make frontdoors; do not
invoke those Plans as portable user examples.

The shipped catalog stores compute package identities and classifications from
the current tree. The [inventory owner](TEMPLATE_CATALOG_DETERMINISM.md)
separates this source census from proof that a workflow ran. Teaching, internal,
external-prerequisite and preview fixtures must retain their documented limits.
A direct subprocess fixture does not prove Fleet plus Game Tape execution.

Deterministic parser/compiler/CLI regressions establish behavior at their tested
boundaries. Human and coding-agent blind authoring trials across all eight
categories and a controlled pre/post productivity comparison are **not measured**
by those tests. No percentage improvement, native qualification, external
registry/cluster success or release readiness follows from shorter YAML.
