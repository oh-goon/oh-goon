# Plans and Goons

Oh Goon has one deliberately small authoring model:

```text
Repository
  → Plan
  → Goon
  → Step
  → Execution / Verification / Evidence

Atomic Goon    → Steps
Composite Goon → Goons
```

A Goon contains `steps:` or `goons:`, never both. Plans select Goons and never contain Steps. Execution stays ordered and linear: there is no DAG, inheritance, YAML macro system, patch/override model, or second dependency model.

## Goon Simple Contract

Normal Goon authoring should answer only three questions:

1. Which inputs are genuinely user-controlled?
2. Which locked execution environment is needed?
3. Which understandable command should run?

A normal Python Goon is intentionally this small:

```yaml
version: 1
uv:
  group: dev
steps:
- run: uv run pytest
```

`uv:` means Oh Goon prepares a locked UV project environment. `uv.lock` remains the dependency authority; the Goon does not repeat package names, lock hashes, cache keys, warmup steps, or manual `uv lock --check` commands. `group:` selects how the same lock is used; it does not redefine dependencies.

`run:` is parsed into argv and executed directly without a shell. Shell operators, redirects, command substitution, multiline shell, and inline interpreter programs such as `python -c` or `sh -c` are not normal authoring. If an operation needs real program logic, expose it as a project command such as `uv run verify-release`. If generic reusable logic is needed, build a real tool or backend-owned typed capability rather than a scripts subsystem in the template catalog.

Step ids are optional. Oh Goon derives stable resolved ids for ordinary Steps. Declare `id:` only when later dataflow refers to the Step or a semantic name materially improves the Game Tape.

Do not put execution bookkeeping in normal Goon YAML. In particular, avoid declarations for facts the backend already owns: repository identity, default timeout/resources, UV cache lineage, command exit status, stdout/stderr, tool invocation evidence, or duplicate status JSON. Declare artifacts only when the command creates a real result worth retaining.

## Template path grammar

Every shipped template is exactly one package:

```text
templates/goons/<category>/<name>/
├── goon.yml
└── README.md

templates/plans/<category>/<name>/
├── plan.yml
└── README.md
```

Shipped Plan/Goon packages omit `name:` and `description:` from YAML. The logical
path is their identity (`python/test`); human description belongs in `README.md`.
The general Goon compiler accepts optional readable metadata, but that does not
change the shipped catalog policy. `version:` identifies the file contract.

Shipped catalog classification is derived from that same canonical category path and exposed by the backend catalog; it is not authored into execution YAML or inferred from README prose. `python/`, `security/`, `oci/`, and `kubernetes/` are production templates; `advanced/`, `general/`, and `ml/` are teaching examples; `oh-goon/` is internal self-hosting. A new shipped category must first receive an explicit classification. Production templates cannot depend on teaching or internal Goons.

Imports use the logical path directly:

```yaml
version: 1
goons:
- python/ruff
- python/test
```

A binding is explicit only when needed:

```yaml
- import: python/test
  with:
    coverage_threshold: ${{ inputs.coverage_threshold }}
```

There is no recursive lookup, package alias, extension guessing, inheritance,
merge, patch, overlay, implicit parent-input propagation, or deduplication.
An optional `as:` on a Plan/Composite `import:` mapping names that one compiled
occurrence; it never changes package lookup. See
[import occurrences](../docs/development/GOON_AUTHORING.md#import-occurrences).

## Atomic and Composite Goons

Atomic Goons own executable Steps. Small capabilities are the reference model: `python/ruff`, `python/typecheck`, `python/test`, and `python/uv-audit`.

Composite Goons only compose Goons:

```yaml
version: 1
goons:
- python/ruff
- python/typecheck
- python/test
```

Written order and duplicates are preserved. Explicit input bindings remain explicit across composition boundaries.

Typed infrastructure Goons such as OCI or Kubernetes may need more detail because they represent credentials, mutation, registries, or other security boundaries. Their necessary detail must not become ceremony imposed on ordinary command Goons.

## Plan

A shipped Plan can be as small as:

```yaml
version: 1
goons:
- python/quality
- security/cve-combined
```

The catalog path supplies identity and the normal shipped execution purpose is CI/CD. One-Goon Plans are valid when Plan is the useful execution/admission boundary; they should be removed only when they have no architectural purpose, not merely because they are short.

Plans cannot import Plans. Goons cannot import Plans. Reusable capability groups belong in Composite Goons.

## Resolution and evidence

Authoring remains hierarchical, execution remains sequential, and provenance remains hierarchical. The resolver validates the composition graph once and produces the resolved execution model used by preview, execution, receipts, diagnostics, and Game Tape.

`argv`, generated Step identity, lock enforcement, cache lineage, default runtime limits, and execution evidence may exist in that resolved/backend model without becoming normal authoring syntax.

GitLab migration guidance lives in `docs/development/GITLAB_MIGRATION.md`; migration artifacts do not belong in the template tree.

## Review rule

When reviewing a Goon, ask: **does this YAML declare something the execution boundary already knows?** If yes, remove it from authoring unless it is an explicit security or domain boundary.

For a shipped Goon or Plan change, run the focused template tests first, then architecture/backend checks. Do not add compatibility lookup, sidecars, helper DSLs, Plan Steps, or another execution path to make authoring shorter.

## Minimal Plan input projection

A Plan may explicitly expose a directly selected Goon input without copying its schema:

```yaml
version: 1
inputs: [coverage_threshold]
goons:
- python/quality
```

The selected Goon remains the authority for type, choices, defaults, ranges, and validation. Projection is not parent-input inheritance: the name must belong to exactly one directly selected Goon or resolution fails. Use the explicit `with:` form only for a real binding, such as feeding a prior Goon output or a concrete Plan value into a selected Goon input.

## Composite input projection

Composite Goons expose child-owned input contracts without copying their schemas:

```yaml
version: 1
inputs: [containerfile, context, platform]
goons:
- import: oci/image
  pass: [containerfile, context, platform]
```

`pass: [containerfile, context, platform]` is exactly equivalent to the three
legacy `with:` bindings `containerfile: ${{ inputs.containerfile }}` and so on.
It is legal **only on one Composite Goon import mapping**. The parent must
declare each name in its `inputs:` list and the selected child must own the
matching typed input. It never broadcasts to sibling Goons or inherits inputs.

For example, an imported Goon may also receive an explicit producer value:

```yaml
version: 1
inputs: [publish_registry_hostname, publish_repository]
goons:
- import: oci/publish
  pass: [publish_registry_hostname, publish_repository]
  with:
    source_workspace: ${{ goons.oci-image.workspace }}
```

This fragment shows the two binding forms; a runnable Composite also needs
the preceding `oci/image` producer (as in the shipped `oci/publish-image`).
`pass:` and `with:` must bind disjoint child names; collisions, duplicates,
unknown names and non-list `pass:` are errors **before work**. Renamed inputs,
external `from:` sources, workspace and output references stay in `with:`.
There is no Plan-level `pass:`, wildcard, `all`, implicit matching, or Step-level
`pass:`. The child remains the schema/default/validation owner. Compatible
child contracts may share an explicit parent input; incompatible contracts
fail the existing source-graph resolution. See
[input resolution and transport](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).

## Safe input resolution

A caller may normalize external names into one canonical Goon input before execution:

```yaml
- import: kubernetes/deploy
  with:
    timeout:
      from:
      - env: HELM_TIMEOUT_OVERRIDE
      - env: HELM_TIMEOUT
```

The first defined source wins, then the child's visible default applies.
Repository YAML, TOML, one-line text files, and verified Git metadata are also
supported:

```yaml
version:
  from:
    yaml:
      path: project.yml
      key: project.version
pyproject_version:
  from:
    toml:
      path: pyproject.toml
      key: project.version
version_file:
  from:
    text:
      path: VERSION
revision:
  from:
    git: commit_short_sha
```

`text` accepts exactly one non-empty trimmed line. Structured file sources use
simple dotted keys and stay repository-contained. Resolution never runs shell,
jq, yq, or user expressions. It produces one value and then the child Goon's
normal input contract validates it. Secret bytes do not belong in this
mechanism; keep credentials on explicit secret/environment authority.

Readable contracts can use small constraints and semantic types:

```yaml
image_name:
  type: string
  starts_with: my-image-storage/
platform:
  type: choice
  choices: [linux/amd64, linux/arm64]
  default: linux/amd64
timeout:
  type: duration
  default: 5m
```

If values must be combined, branched, formatted, or otherwise computed, put that logic in normal project code and expose a real command such as `uv run resolve-helm-release`. **Validation/resolution can be declarative. Business/project computation belongs in project code.**

## Resolution versus transport authority

The one [template input resolution and transport guide](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md)
explains exactly when external values, projected input contracts, producer output
references, and execution-time frozen resources become authoritative. `README.md` is
not a second source schema: refer to the declared Goon inputs and the canonical
resolver. Inputs are resolved before work; Step outputs are consumed only after their
own producer reports them. No Step may perform a fallback resolution pass.

For file artifact retention in shipped Goons, see the
[artifact retention authoring contract](../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

For deliberate outbound network requirements, follow the
[canonical template network authority](../docs/development/TEMPLATE_NETWORK_AUTHORITY.md),
which keeps native scanner network needs separate from implicit tool permission.

## Self-hosting evidence boundaries

See the [canonical self-hosting boundary guide](../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md)
for the integration source-workspace distinction and browser preflight ownership.

For the single canonical discovery owner, stable catalog ordering and byte-exact
Plan/Goon source digests, see
[template catalog determinism](../docs/development/TEMPLATE_CATALOG_DETERMINISM.md).

For read-only contract provenance in the shipped catalog, see
[template input inspection](../docs/development/TEMPLATE_INPUT_INSPECTION.md).
This is not an authoring schema or a second input resolver.

## Inspect and configure inputs

Before configuring a Plan, inspect the exact canonical typed inputs and owners:

```bash
uv run --offline --frozen --no-sync --project backend oh-goon plan inputs templates/plans/python/quality/plan.yml
uv run --offline --frozen --no-sync --project backend oh-goon plan inputs templates/plans/python/quality/plan.yml --json
```

This is read-only **contract inspection**. It displays the existing child-owned
type, default, constraints and explicit owner paths; it neither selects a value
nor validates or admits a Run. `pass:` remains a shorthand for an explicit
Composite import binding. No host secrets or runtime producer outputs are read.
The JSON `root_source_sha256` is the root file digest, **not** an imported-graph
closure identity. Details: [canonical input inspection](../docs/development/TEMPLATE_INPUT_INSPECTION.md).

### Caller values files

Use `oh-goon plan inputs <plan.yml>` to discover the canonical input interface;
then `oh-goon plan validate <plan.yml> --inputs-file values/staging.yml` or
`plan explain` to validate explicit scalar caller values in a strict bounded
`inputs:` mapping. A value file is **not** a Composite `pass:` replacement or a
host secret file. `--input` may coexist only for different names. `plan run`
uses the original Run ingress preflight and queued source-freshness checks.
`plan schedule` and `reschedule` store immutable resolved values and source
provenance in the original Schedule. A manual Run checks the live file; a
scheduled occurrence never reloads it. See
[caller-file custody](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md#caller-selected-input-files).

### Scheduled caller configuration files

Schedule creation and replacement may use the same strict `--inputs-file`
syntax as Plan validation. A Schedule saves the **resolved** values and an
immutable source SHA-256 receipt at creation time. Later edits to the YAML
file do not change a Schedule automatically: use `plan reschedule` to select
new values. A scheduled occurrence never needs the creator's file to remain
mounted. The [scheduled values contract](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md#scheduled-values-and-provenance)
describes the receipt and generation boundaries.

### Explain recipients and validation failures

```bash
uv run --offline --frozen --no-sync --project backend oh-goon plan explain templates/plans/python/quality/plan.yml --routes --json --input coverage_threshold=85
```

`--routes` shows exactly which imported Goon occurrence receives each
normalized parameter, including intermediate Composite forwarding. It also
names producer **reference identities** for workspace and output edges without
pretending the producer has executed. Report schema:
`oh-goon.plan-input-routing/v1` with status `resolved_not_executed`.
Existing `plan explain --json` is unchanged. The root SHA-256 is the root file
only; no new admission authority or secret/value transport is introduced.
Details: [resolved recipient routing](../docs/development/TEMPLATE_INPUT_INSPECTION.md#resolved-recipient-routing).

When a selected value or contract is invalid, use `oh-goon plan validate
<plan.yml> --diagnostics --json` or `oh-goon plan explain <plan.yml>
--routes --diagnostics --json` to get a typed, redacted repair hint rather
than the legacy exception text. Valid Plans produce the exact existing success
report. This is a view of the canonical input owner, **not** another compiler,
second source of values, execution admission or readiness claim. See
[safe rejection diagnostics](../docs/development/TEMPLATE_INPUT_INSPECTION.md#safe-rejection-diagnostics).

## Authoring guides and evaluation

See [canonical-source input journeys](../docs/development/TEMPLATE_INPUT_INSPECTION.md#evaluation-and-proof-scope)
for the frozen real-source HTTP correction journeys and separate browser
presentation coverage. Neither an accepted inspection nor evaluation results
is an execution admission or deployment qualification.

For concise Atomic/Composite/Plan input examples, exact `pass`/`with`/`from`
ownership, team values-file custody and original import-occurrence IDs, see
[Goon authoring](../docs/development/GOON_AUTHORING.md) and
[input resolution and transport](../docs/development/TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md).
No wildcard output forwarding or additional execution path is introduced.

The [UX-D authoring and evidence guide](../docs/templates/TEMPLATE_UX_D_EVALUATION.md)
explains the existing single-compiler discovery, draft, input and source
inspection workflow, the reproducible 41-Goon/24-Plan catalog check, and the
explicit difference between scripted contract evidence and **unmeasured**
human/agent authoring improvements. Read-only authoring is not an alternate
admission or execution path.
