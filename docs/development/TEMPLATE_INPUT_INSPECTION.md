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

After [development setup](../DEVELOPMENT.md), invoke the source CLI with
`uv run --offline --frozen --no-sync --project backend oh-goon` in place of
`oh-goon` below. Built releases use the installed command directly.

```sh
oh-goon templates list --kind plan
oh-goon league add-folder /absolute/path/to/repository
oh-goon templates init python/quality --kind plan --output ./workflow --repository /absolute/path/to/repository
```

`--kind` is required because a Plan and a Goon may share the same catalog key.
For a single Goon, use `oh-goon templates init python/test --kind goon --output ./workflow`.
A Plan selection uses the catalog directly; a Goon selection creates a minimal
`plan.yml` importing that exact Goon and selects the file from `run.yml`. Neither
mode copies the selected Goon implementation or invents another source owner.

The initializer resolves `--repository` through the existing League registry
and writes its canonical identity. Edit the generated inputs you need. For example:

```yaml
version: 1
source:
  kind: catalog-plan
  name: python/quality
target:
  repository_id: REPOSITORY_ID
inputs:
  project: backend
  dependency_group: dev
  typechecker: ty
```

The illustrative `REPOSITORY_ID` above is filled by `--repository`; omitting that
option deliberately leaves a target placeholder for later selection. An
unregistered explicit path refuses before publishing the workflow. See the
[template first-run guide](../../templates/README.md) for headless registration.
The Python values above illustrate this repository's layout. Native uv, Ruff,
Pytest and the selected type checker retain their project configuration and
results. Inspect your actual selected contract before changing values.

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config inspect ./workflow/run.yml --json
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
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
selectors independently of `--update-skills`. Inspection and validation use
cached source only; unavailable source reports acquisition is required without
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
