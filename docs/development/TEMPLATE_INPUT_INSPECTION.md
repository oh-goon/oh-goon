# Run configuration and inspection

Normal CLI execution selects one explicit `run.yml`. It chooses an existing
Plan or repository Goon, a registered target, scalar caller values, and relevant
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
oh-goon init --template python/quality --repository /absolute/path/to/project --no-input
oh-goon run ./workflow --no-input
```

Workflow invocation preserves existing execution history and League projections.
Unresolved earlier work is refused through the original admission guards; use
the matching runtime's recovery tools to settle its authority. Application
startup retains recovery and reconciliation. A new operational database may
derive its initial projection from already-saved League preferences; this does
not register additional targets. Missing Run locks refuse before source work
or runtime construction.

`--kind` defaults to `plan`; a single Goon uses `--kind goon`. An unchanged Plan
stays selected directly from the catalog. A Goon selection creates a minimal
`plan.yml` importing that exact Goon. The default output directory is `workflow`.

Normal initialization requires an explicit exact repository root. It validates
known source/input/output conditions first, registers only that Git root through
the existing League/LocalState owner when needed, and publishes the complete
workflow with the first canonical lock in one exclusive native rename. Existing
registered filesystem subjects remain supported. A container directory is not a
request to register child Git roots. Registration is a separate durable effect:
if later preparation/publication fails, the result reports whether registration
committed and whether a complete workflow became visible. A publication whose
final durability check fails remains visible and is reported incomplete; it is
never silently rolled back over a concurrent writer.

The new directory must not exist, even if empty. Unsupported native exclusive
publication fails before commit. Failed precommit scratch is removed only under
its retained ownership; foreign/replaced entries are preserved.

Supply typed caller choices directly, for example:

```sh
oh-goon init --template python/quality --repository /absolute/path/to/project \
  --input project=backend --input dependency_group=dev --input typechecker=ty --no-input
```

`--input NAME=VALUE` transports literal text to the original compiler's coercion
and constraints. For a string input, `--input label=null` means the string
`null`; `--input-null label` supplies explicit null. Names may occur only once
across both flags. Missing required values may be prompted only in an interactive
TTY. `--no-input`, `--json`, non-TTY input and EOF refuse missing values before
registration or output publication. Optional omissions retain compiler defaults.

Use `init --draft` when authoring an incomplete Agent or bounded-repair workflow.
It omits missing fields, reports NOT_READY and required actions, and publishes
no lock or Run. It does not register a target. Complete the v2 selection and
explicitly prepare it; placeholder strings are not admission evidence.

`run.yml` uses `version: 2` and exactly one of `target.repository` (one path) or
`target.repositories` (at least two distinct paths). A relative path resolves
from that exact file's parent; an absolute path is explicit. Repeat init's
`--repository` to select an exact set. Catalog, file and Git Plans resolve one
source graph for the full set without inventing a primary repository.
Repository Goons, Agent and repair workflows remain singular.
Selecting a directory means exactly its `run.yml`; there is no upward search,
CWD default, Git-ancestor fallback or symlink-component traversal. Internal
repository IDs and instances remain runtime authority and are not authored.

Every selected root is checked before registration or source acquisition.
Duplicate, missing, excluded or replaced members refuse the full selection;
the CLI never drops one or expands it into siblings. Registration is explicit
for each selected root, and partial durable registrations are reported if a
later registration, lock or publication operation fails.

After reviewing source/input changes:

```sh
oh-goon workflow prepare ./workflow
oh-goon run ./workflow --no-input
```

Inspection and validation are optional read-only diagnostics:

```sh
oh-goon workflow inspect ./workflow --json
oh-goon workflow validate ./workflow
```

They do not initialize/repair a database, register a target, write a lock, contact
a model or execute a Step. Inspection can describe an unregistered target with
registration unavailable; validation/preparation/Run require current registration.
Fresh CI explicitly uses `workflow prepare ./workflow --register-target --no-input`.
Default read operations do not acquire sources; `--acquire` explicitly allows the
existing Git owner to acquire them. A successful validation is not an executed
check or APP PASS.

For a new Git selection, `oh-goon workflow inspect ./workflow/run.yml --acquire`
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
version: 2
source:
  kind: git-plan
  git: https://github.com/your-org/workflows.git
  ref: main
  path: examples/orders
  plan: plan.yml
target: {repository: /absolute/path/to/project}
```

`path` is the authoring root inside that Git revision; `plan` is an exact
contained path relative to it. The Plan's local `from:` imports remain inside
that selected root and revision. Selecting a single remote Goon instead uses
an explicit Git `from:` mapping in the local Plan; see
[Goon authoring](GOON_AUTHORING.md#project-owned-goons-and-caller-overrides).
Neither selection searches for a nearby Plan or shadows shipped imports.

`workflow prepare` explicitly acquires source and records its exact commit in `goon.lock`.
Ordinary lock regeneration and runs keep the pin; a missing cache may recover
that same commit. `workflow prepare run.yml --refresh goons` explicitly refreshes Goon/Plan
selectors independently of `--refresh applications` and `--refresh skills`.
Inspection and validation use cached source by default; unavailable source reports acquisition is required without
network effects. Acquisition does not run project code or install dependencies.
Explicit `workflow inspect --acquire` or `workflow validate --acquire` permits acquisition for contract
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

Run `oh-goon workflow prepare ./workflow/run.yml` after reviewing a change to `run.yml`,
Plan execution policy, ordered imports, Goon behavior or requirements, or captured
application code, metadata, native lock or resources. Target-project dependencies
remain native uv authority; `goon.lock` does not replace `uv.lock` or prove that
dependencies are installed. Ordinary relocking preserves recorded Git Plan/Goon,
application and Skill commits. Select `--refresh goons`, `--refresh applications`
or `--refresh skills` only when you intend to refresh that source class.

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
source names. Read the selected contract with `workflow inspect` when you need
input definitions. A refusal never refreshes pins or repairs a lock implicitly.

## One human and JSON report

`workflow inspect` renders the same local captured selection as `--json`.
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
oh-goon runs show RUN_ID
oh-goon runs steps RUN_ID
oh-goon runs outputs RUN_ID
oh-goon runs handoff RUN_ID
```

These views reopen the original durable Run and Game Tape after the execution
process exits. `runs outputs` reports hashes, byte counts and retained references
when present; it does not download artifact bytes. An `evidence` artifact does
not promise retained file bytes. Read `oh-goon runs logs RUN_ID` while transient
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
