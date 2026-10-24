# Run configuration and inspection

Normal CLI execution selects one explicit `run.yml`. It chooses an existing
Plan or repository Goon, a registered target, scalar caller values, and relevant
agent or bounded-repair selections. It does not define Steps, permissions,
provider connections, native model settings, or a second workflow language.

This single-file entrypoint applies to local CLI catalog/file/repository
invocations. The browser's persisted Team and Shared Plan APIs retain their
distinct source-revision, repository-instance and reviewed-target authority.
They converge on the same Fleet execution lifecycle, but the browser does not
load `run.yml` or expose its new agent/repair groups. No equivalence for those
new groups across CLI/API/GUI is claimed.

After [development setup](../DEVELOPMENT.md), invoke the source CLI with
`uv run --offline --frozen --no-sync --project backend oh-goon` in place of
`oh-goon` below. Built releases use the installed command directly.

```sh
oh-goon templates list --kind plan
oh-goon templates init python/quality --output ./workflow
```

Edit the generated `workflow/run.yml` to select the registered repository ID
and the inputs you need. For example:

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

`REPOSITORY_ID` is a placeholder for the target's registered canonical identity.
Register the repository in the UI and copy that ID; a path is not an identity.
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
write a lock, fetch remote Skills, contact a model, read native private config,
or grant permission. Locking and execution use the original resolver and
Fleet lifecycle. A completed validation is not an executed check or APP PASS.

## Source and value owners

Choose exactly one source:

| `source.kind` | Required selection | Meaning |
| --- | --- | --- |
| `catalog-plan` | `name: category/name` | Exact shipped Plan; no copied Plan required. |
| `file-plan` | `path: ./plan.yml` | Exact project Plan contained below the selected workflow root. |
| `repository-goon` | No `name` or `path` | Exact registered target's root `goon.yml`. |

Mixed kinds, unknown keys or versions, duplicate YAML keys, unsafe paths,
missing sources and unknown targets refuse before effects. Selection never
searches another source or merges repository YAML with a Plan. Local Plan
imports use the normal explicit `from: ./goons` source grammar.

`inputs` supplies scalar values only. The imported Goon and canonical binder
own names, types, defaults and constraints. An omitted value may use its
owner's default; an explicit invalid value fails. Ambiguous shared inputs need
explicit `pass:` recipients or renamed `with:` bindings in the Plan/Goon.
The config cannot contain copied schemas, nested executor policy, credentials,
secret interpolation, `steps`, `goons` or native `modelOverrides`.

Optional `agent` and `repair` groups select existing invocation facts only.
Use them only for workflows that support those selections. See
[Agent Skills](../AGENT_SKILLS.md) and [Agent providers](../AGENT_PROVIDERS.md).
`.goonskills.yml` alone owns Skill source/ref/directories; `goon.lock` owns
frozen Skill commits. Provider catalogs own connection/profile definitions.
Pi, OpenCode and Codex own their native authentication, context windows,
compaction, reasoning, tools and private settings.

An `agent` selection requires `acceptance`, an absolute approved
`repository_root`, and a contained relative `skill_root`. Select either native
`implementation` plus `profile`, or managed inference `model_profile`; a native
selection cannot also set a managed model profile. `repair` requires all four
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
