# Template input resolution and transport

This is the owner-facing guide for the **shipped** Plan/Goon catalog. It describes the existing
execution contract; it does **not** define another authoring schema, resolution service, or
step-time fallback. Machine truth is in `templates/{plans,goons}/**/*.yml` and in the
canonical Fleet resolver/admission/executor. The catalog READMEs explain intent only.

## Authority, in order

| Boundary | One authority | What it decides / may carry | Must not do |
| --- | --- | --- | --- |
| Source selection | Repository `goon.yml` **or** selected Shared `plan.yml` | One captured run.yml source selection and explicit registered target | Merge sources or fall back to repository YAML in a Shared Run |
| Composition | `FleetPlanResolver` plus canonical catalog/source graph | Ordered Plan → imported Goons → Steps; imported paths, unique unbound Plan inputs, explicit `with:` edges (or import-local `pass:` compiled to the same edges), step/output reference identity | Broadcast shared names, invent schemas, synthesize Plan Steps, search aliases, re-resolve inside Steps |
| Caller values | Captured run.yml selection and the same canonical input binder | One scalar `inputs` mapping, without overlays or flag precedence | Accept duplicate YAML names, retry an invalid explicit value through another source, or let run.yml define input schemas |
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

**1. Projection exposes child-owned contracts.** The shipped
[`python/quality` Plan](../../templates/plans/python/quality/plan.yml) exposes
`project`, `dependency_group` and `typechecker`. Its
[`Composite Goon](../../templates/goons/python/quality/goon.yml) explicitly selects
recipients with `pass:`. The
[`type-checking Goon](../../templates/goons/python/typecheck/goon.yml) owns the checker
choices; Ruff, tests and type checking own compatible native environment contracts.
The Plan needs only `goons: [python/quality]`; it does not repeat the Composite's
public inputs or internal bindings. Unbound public names with one direct owner
are automatically available to caller values. Fixed `with:` values are excluded.
Shared names retain independent child defaults, and caller overrides are refused
until `pass:`/`with:` explicitly selects recipients. Optional Plan `inputs:`
restricts its public interface. Composite Goons always declare their interface;
hidden child inputs do not become public. Neither Plan nor README copies a schema.

**2. Explicit `with:` is a real edge.** The shipped
[`oci/release-deploy` Plan](../../templates/plans/oci/release-deploy/plan.yml) passes
`${{ goons.oci-publish-image.outputs.OCI_REFERENCE }}` to `kubernetes/release.image`.
The [`oci/publish-image` composite](../../templates/goons/oci/publish-image/goon.yml)
passes `${{ goons.oci-image.workspace }}` to its publisher. Output and workspace
references are typed producer-to-consumer edges; they are not arbitrary interpolated
host paths or pre-existing environment values. Consumer capabilities remain responsible
for validating the produced binding.

On a **Plan or Composite Goon import**, `pass: [namespace, deployment]` is an
authoring shorthand for `with: {namespace: '${{ inputs.namespace }}',
deployment: '${{ inputs.deployment }}'}` on that exact recipient. It is expanded
by the existing YAML compiler into the same `PlanInputBinding` type before
source-graph materialization. The shorthand has no value source of its own;
it never introduces inheritance, environment reads, implicit forwarding,
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

## One selected run configuration

The normal CLI selects one versioned `run.yml`; it captures the configuration
bytes, contained path and file identity once. Its scalar `inputs` mapping feeds
the original binder. The config does not copy child schemas or define a second
value-resolution precedence. `workflow inspect`, `workflow validate`, `lock` and
`run` consume that one selection. See the
[configuration guide](TEMPLATE_INPUT_INSPECTION.md) for the supported grammar.

The target's freshness owner verifies the captured config identity at execution
boundaries. A changed, missing, replaced or inaccessible file refuses before
effects. The receiver consumes resolved authority rather than rereading the
file into a new semantic decision. A captured filename/digest is provenance;
it is not an imported-source-closure proof or admission.

Service-owned Shared Plans and Schedules retain their distinct reviewed-source,
execution-digest and generation contracts. Accepted Schedules freeze resolved
values and source provenance rather than rereading a creator's config on every
occurrence. Creating a new Schedule is a separate explicit operation; run.yml
is not an iterator or scheduler.

## Routing presentation

### Retained configuration decisions

The original resolver captures bounded, value-free `PlanConfigurationEvidence`
on the resolved Plan. It records each input/environment name and occurrence,
source digest and available YAML marks, selected origin, structural parent or
producer edge, and external-source candidate outcomes. Missing, null and empty
external candidates retain their existing fallthrough behavior. Candidates
after the first defined source are `not_evaluated` without being read. Selection capture records the run.yml origin before the existing immutable
target is prepared; Schedules retain that selection without reopening the file.

The Journal retains this metadata with the target in `RUN_ADMITTED`. It is outside
semantic Plan/lock identity; the target verifies known source/occurrence digests.
Marks come from the same parsed syntax tree as compilation. A projected root
input may have no root YAML mark. The trace retains at most 4096 decisions and
reports the omitted count without changing execution membership. Values,
defaults, patterns, file contents and native argv are not copied into this trace.

`workflow inspect` and `workflow validate` return safe configuration and authoring
explanations without admission. Their human and JSON output use the same captured
selection and canonical owner projections. `runs tape RUN_ID [--json]` and
`dev explain-run RUN_ID --json` expose retained configuration through Game Tape,
without rereading source or resolving current values. Missing historical lineage
is unavailable, not reconstructed.

This does not add a generic preflight, native feasibility probes, new input
contracts or execution authority. Existing admission, preparation and native
execution owners retain their decisions.

Canonical input-routing projection preserves exact compiled import occurrences,
active recipient bindings and producer references. It never reveals input values
or Step results, and it never treats a root digest as closure or admission.

## Structured validation refusal

The canonical input binder and projected contract materializer attach typed
rejection metadata for safe configuration diagnostics. Presentation maps only those typed codes to user-readable next
actions; arbitrary exceptions become a generic refusal without raw source
paths or values. This changes no input precedence, Run preparation, resolution
snapshot, or admission authority. See
[configuration reporting](TEMPLATE_INPUT_INSPECTION.md#one-human-and-json-report).

## Explicit local Goon sources

An import may select `from: ./goons` beside its static `category/name` identity. The existing source graph resolves that exact package inside the root authoring workspace, keys edges by import occurrence and reads each unique source once. Omitted `from:` retains shipped catalog selection; there is no local shadowing or fallback. Source positions retain local paths, while locks continue covering resolved execution semantics. File-backed Plans and repository Goons support local catalogs; Shared Plan storage refuses them because it saves only the Plan file.

An explicit Git `from: {git: URL, ref: REF, path: exact/category/name}` selects
one package. A `git-plan` run selection captures an exact Plan and its contained
local packages at one revision. Both feed this same source graph and binder;
`goon.lock` retains their exact commits, and `workflow prepare --refresh goons` is the explicit
source refresh. Read-only inspection uses cached source only. See
[Goon authoring](GOON_AUTHORING.md#project-owned-goons-and-caller-overrides)
and [run configuration](TEMPLATE_INPUT_INSPECTION.md#source-and-value-owners).

## Native uv selection

The Goon's uv project/group may reference declared inputs. They are bound before
requirements, native preparation or execution. Omit `group` for base project
dependencies with no default groups; an explicit group adds its dependencies.
`uv.lock_mode` is a literal `frozen` (the default) or `locked` selection, retained
with the resolved Step and requirement. Frozen consumes the existing native lock
without a freshness check; locked asks native uv to reject a required change.
Neither selection refreshes native resolution. Derived uv requirements remain
with the declaring Atomic Goon and are supported
for ordinary verification as environment requirements. CI/CD-only repository
requirements remain CI/CD-only.

For target-project uv, the working directory defaults to the selected project;
an explicit working directory is supported and the compiled uv project argument
is relative to it. Authored output/artifact paths become concrete
repository-relative execution paths. An Atomic Goon with `runtime: python`
instead owns the complete project beside `goon.yml`; `uv.project` cannot redirect
it. The captured project and selected group travel with the resolved command.
Native uv installs that package in an owned environment; relative produced
files resolve under writable attempt state, separately from package source and
the target repository. Code, resources, metadata and the native lock participate
in execution freshness without changing the catalog YAML digest contract.
