# Template input resolution and transport

This is the owner-facing guide for the **shipped** Plan/Goon catalog. It describes the existing
execution contract; it does **not** define another authoring schema, resolution service, or
step-time fallback. Machine truth is in `templates/{plans,goons}/**/*.yml` and in the
canonical Fleet resolver/admission/executor. The catalog READMEs explain intent only.

## Authority, in order

| Boundary | One authority | What it decides / may carry | Must not do |
| --- | --- | --- | --- |
| Source selection | Repository `goon.yml` **or** selected Shared `plan.yml` | Exact mutually exclusive source and selected repository set | Merge sources or fall back to repository YAML in a Shared Run |
| Composition | `FleetPlanResolver` plus canonical catalog/source graph | Ordered Plan → imported Goons → Steps; imported paths, projected inputs, explicit `with:` edges (or import-local `pass:` compiled to the same edges), step/output reference identity | Infer inputs, synthesize Plan Steps, search aliases, re-resolve inside Steps |
| Caller values | CLI caller-input collector and the same canonical input binder | Explicit `--input` values and a captured `--inputs-file`, with disjoint input names | Overlay duplicates, retry an invalid value through another source, or let the file define input schemas |
| Input source | `fleet_plan_input_resolution.resolve_input_sources` | First **defined** `from:` source: `env`, repository-contained `yaml`/`toml`/one-line `text`, or native Git fact | Run shell/expressions, infer missing values, copy secrets into input sources |
| Contract | `core.fleet_plan_composition.bind_inputs` and owning Goon input definition | Validate/coerce the selected value, apply child's declared default **only when no source was defined**, reject unknown/missing/invalid inputs | Let Plan/README repeat or relax child schemas |
| Admission | `FleetAdmissionPlanner` and `RUN_ADMITTED` receipt | Prove source subject, resolved Plan and repository requirements before effects | Run a probe Step to discover predictable invalid input/requirements |
| Strict run preparation | `_FleetPlanRunInputs` / canonical execution authority | Freeze admitted source snapshot, native toolchain, host-env names/values and step-specific bindings | Reopen candidate input sources or silently choose a replacement Plan/tool |
| Execution and reporting | Isolated Step attempt, typed outputs, Journal / Game Tape | Consume resolved command argv/typed capability and permitted producer output/workspace references; report evidence once | Give a child Step a new input resolver or turn a missing output into a guessed string |

**Resolve once → transport once → execute once → report once.** This means one semantic
decision per boundary, not that runtime results exist at compile time. An earlier Step's
**output reference identity** is bound during Plan resolution; its actual output value
exists only after the producer finishes and is validated by the execution/evidence owner.
Do not claim that the compiler executes producers or eagerly resolves their output bytes.

## Input ownership: three distinct cases

**1. Projection is an interface, not inheritance.** The shipped
[`python/quality` Plan](../../templates/plans/python/quality/plan.yml) exposes
`inputs: [coverage_threshold]`. The owning
[`python/test` Goon](../../templates/goons/python/test/goon.yml) defines the actual integer
range/default; the intermediate
[`python/quality` composite](../../templates/goons/python/quality/goon.yml) explicitly
forwards it. Projection of a name across two directly selected Goons is ambiguous and
must fail. Neither Plan nor README owns a copied validation rule.

**2. Explicit `with:` is a real edge.** The shipped
[`oci/release-deploy` Plan](../../templates/plans/oci/release-deploy/plan.yml) passes
`${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}` to `kubernetes/release.image`.
The [`oci/publish-image` composite](../../templates/goons/oci/publish-image/goon.yml)
passes `${{ goons.oci-image.workspace }}` to its publisher. Output and workspace
references are typed producer-to-consumer edges; they are not arbitrary interpolated
host paths or pre-existing environment values. Consumer capabilities remain responsible
for validating the produced binding.

On a **Composite Goon import only**, `pass: [namespace, deployment]` is an
authoring shorthand for `with: {namespace: '${{ inputs.namespace }}',
deployment: '${{ inputs.deployment }}'}` on that exact recipient. It is expanded
by the existing YAML compiler into the same `PlanInputBinding` type before
source-graph materialization. The shorthand has no value source of its own;
it never introduces inheritance, environment reads, Plan-level forwarding,
runtime output resolution, or admission authority. `pass:` and `with:` may be
combined for **different child targets** only; producer and workspace references
remain in `with:`. A duplicate target or an implicit caller-level fan-out is an
error. See [Composite authoring](GOON_AUTHORING.md#composite-inputs-expose-child-contracts).

**3. `from:` selects an external scalar at resolution, not execution.** The supported
source kinds are `env`, `yaml`, `toml`, `text`, and `git` with Git fields
`commit_sha`, `commit_short_sha`, `branch`. Sources are examined in authored order;
the first defined value wins. An absent environment variable, missing file/key,
empty value, or unavailable optional Git field may yield an undefined source; then the
next source or the child's default applies. A malformed YAML/TOML file, multi-line
text, unsafe repository path, unsupported scalar type, or non-Git repository for a
required Git observation is **an error**, not a fallthrough. An explicitly supplied
invalid value is likewise an error, not permission to try a later source. Inputs must
remain free of credentials; secrets use explicit environment/secret capability
boundaries, not `inputs`/README substitutions.

Example of a **binding**, not a new input definition (here `timeout` is already a
child-owned input):

```yaml
- import: kubernetes/release
  with:
    timeout:
      from:
      - env: HELM_TIMEOUT_OVERRIDE
      - env: HELM_TIMEOUT
```

This is illustrative syntax; only references to Goons with an actual corresponding
input declaration are valid. Do not paste this into a shipped template whose child
does not declare `timeout`.

## What crosses the boundary

The resolved object and admission receipt carry the *outcome* of Plan/source selection,
validated input values or structural producer references, and exact repository/source
identity. Strict preparation revalidates the previously admitted repository facts for
staleness and freezes required resources. An author cannot add cache keys, re-read a
host `PATH`, import ambient secrets, append synthetic preflight shell commands, or
translate these facts into a second Step-owned input protocol. A source change between
admission and effect-start is **staleness**; do not turn it into a second independent
choice. `run:` becomes direct argv; explicit shell selection is reserved for documented
shell semantics (see shipped Goon README `Shell authority` where present).

The boundary is **purpose-aware**: source/Plan admission is universal, while strict CI/CD
adds executor/isolation and frozen-tool proofs. Verification must not manufacture strict
CI/CD authority. Backend-managed uv environments are prepared by native uv, not by
copying `.venv/bin` paths or importing Python dependencies into Goon YAML.

## Failure contract and reviewer checklist

- Unknown input, ambiguous projection, invalid source, missing required value,
  output/workspace reference to a nonexistent producer, or cyclic composition:
  reject at the owning resolution/admission boundary before Step work.
- Missing requirements of a selected *imported Goon* retain that Goon's
  `NOT_APPLICABLE` semantics; they are not silently promoted to a Plan-wide requirement.
- Missing or altered physical source/tool/resource after admission: reject as stale or
  unavailable at the existing preparation/binding authority, not by re-resolving.
- Check the shipped Plan/Goons as a single example; confirm who owns each input's
  schema, source, reference and final execution evidence. If a README starts defining
  an extra default, regex, resolution precedence, or shell fallback, delete the duplicate.

For runtime admission/security details see
[CI/CD contract](../CI_CD_CONTRACT.md),
[fail before work](FAIL_BEFORE_WORK.md), and
[template authoring](../../templates/README.md). For the actual implementation see
`services/fleet_plan_resolver.py`, `services/fleet_plan_input_resolution.py`,
`services/fleet_plan_compiled_resolution.py`, `core/fleet_plan_composition.py`, and
`integrations/_fleet_plan_run_resources.py`.

## Read-only inspection UX

For provenance of projected input definitions, see
[template input inspection](TEMPLATE_INPUT_INSPECTION.md). The public
`FleetPlanResolver.inspect_inputs()` result carries the canonical
`PlanInputDefinition` alongside zero, one, or multiple ultimate Goon owner paths.
It never resolves current values, overrides Goon constraints, admits a run, or
executes a Step. The input schema remains owned by the original Goon.

## Caller-selected input files

The Plan CLI accepts a caller-selected scalar mapping for diagnostic commands
(`validate`, `prepare`, `preview`, `explain`, `check`, and `diff`) and for
`run`, `schedule`, and `reschedule`. `--input name=value` and `--inputs-file`
may be combined only for disjoint input names. Invalid explicit values fail at
the original Goon binder; they do not fall back to `from:` or a default. For
`diff`, the same mapping is supplied to both roots and each root's binder
rejects any input it does not accept.

After [development setup](../DEVELOPMENT.md), invoke the source CLI from the
repository root:

```yaml
# values/staging.yml
inputs:
  coverage_threshold: 85
```

```sh
uv run --offline --frozen --no-sync --project backend oh-goon plan validate \
  templates/plans/python/quality/plan.yml --inputs-file values/staging.yml
```

The invocation working directory is the sole caller-file containment root.
The collector rejects traversal, symlink components, missing or nonregular
files, and paths outside that root. YAML must contain one top-level `inputs`
mapping, at most 64 unique canonical names, and at most 65,536 UTF-8 bytes.
Values are scalar strings, integers, booleans, or null. Duplicate keys,
aliases, anchors, merges, tags, expressions, nested mappings, and arrays are
rejected. The file supplies values; the imported Goon still owns their types,
defaults, and constraints. Inputs remain unsuitable for credentials.

One read captures the parsed mapping and an immutable observation of its
caller root, contained relative path, device/inode, and byte SHA-256. This is
file provenance, not an imported-source-closure proof. `lock`, direct Goon
and Shared Plan command families do not acquire this CLI option; `plan inputs`
inspects contracts and accepts no caller values.

### Manual Run freshness

`plan run --inputs-file` completes the existing Run ingress preflight before
reading Plan source or the caller file. The resulting `FleetPlanTarget` carries
the already resolved values and a `caller_input_file` identity containing the
caller root, relative path, device/inode, and SHA-256, without copying file
values into that identity.

The existing Fleet source-freshness owner checks that exact file before and
after target preparation, before admission, and in the queued worker before
`RUN_STARTED` or effects. A changed, missing, replaced, or inaccessible file
fails at that boundary; a remote or restarted worker cannot relocate it or
reparse it into a new value choice. These are freshness checks at defined
boundaries, not an atomic file lock across every Step. Values already frozen
in an admitted target do not change retroactively.

### Scheduled values and provenance

`plan schedule` and `plan reschedule` accept the same input mapping at authoring
time. After the original preparation and freshness checks,
`FleetPlanService._prepare_scheduled_target` converts the live file identity
to an immutable schedule-origin receipt bound to the prepared target execution
digest. The receipt contains the relative filename and YAML-byte SHA-256;
it omits the absolute caller root, inode, file bytes, and raw values.

The existing SQLite Schedule definition retains both the resolved Plan and
that receipt. Future occurrences copy them into the original Fleet Run and
never reread the creator's file. Deleting or editing the file therefore does
not change an accepted Schedule. Choosing new values requires an explicit
`plan reschedule`, with the existing compare-and-swap and generation rules.
A target cannot contain both the manual live-file identity and a frozen
scheduled receipt. Targets created without an input file omit both optional
source fields and retain their existing serialization.

## Routing presentation

`plan explain --routes [--json]` projects exact compiled import occurrences,
active root-input recipient bindings and producer reference identities from
**one existing resolved source cut**. The report exposes neither values nor
Step results. Its root-file digest must not be treated as a source closure
digest, and `resolved_not_executed` must not be treated as admission.
Legacy `plan explain --json`, native resolver, caller-file custody, Fleet
admission and runtime output binding semantics do not change. See
[input routing](TEMPLATE_INPUT_INSPECTION.md#resolved-recipient-routing).

## Structured validation refusal

The canonical input binder and projected contract materializer attach typed
rejection metadata for safe opt-in `plan validate` and `plan explain`
diagnostics. Presentation maps only those typed codes to user-readable next
actions; arbitrary exceptions become a generic refusal without raw source
paths or values. This changes no input precedence, Run preparation, resolution
snapshot, or admission authority. See
[safe rejection diagnostics](TEMPLATE_INPUT_INSPECTION.md#safe-rejection-diagnostics).
