# Template input inspection — read-only contract

`FleetPlanResolver.inspect_inputs(plan_path, containment_root=...)` is a read-only
projection of **the same** canonical analyzed source graph used by
`FleetPlanResolver.input_definitions`. It does not supply values, execute commands,
write admission receipts, or own a registry, schema, or resolver. The underlying
implementation is `services/fleet_plan_input_inspection.py`; the single definition
owner remains the imported Goon and the canonical composition/materialization path.

## What an inspection contains

Each `InputInspection` contains a `definition` (`PlanInputDefinition`: `name`,
`input_type`, `required`, `default`, and any validated constraints) and `owner_paths`.
A path lists each imported Goon from the root toward the ultimate owning Goon.
A directly authored nonprojected root input has **empty** `owner_paths`: never
fabricate a Goon owner. A composite forwarding one input to more than one leaf
can yield multiple owner paths; these are provenance, not alternate validation
rules. An unresolved projected owner is an error, not a guessed path.

The shipped `python/quality` Plan exposes project, dependency-group and checker choices. The project input has ultimate owners in Ruff, type checking and tests; the checker input belongs only to `python/typecheck`. Inspect the current contract rather than copying schemas into documentation:

```python
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

plans = PlanTemplateStore()
source = plans.source("python/quality")
inspected = FleetPlanResolver(goon_templates=GoonTemplateStore()).inspect_inputs(
    source.path, containment_root=plans.root.parent.parent
)
for item in inspected:
    print(item.definition.name, item.definition.input_type, item.owner_paths)
```

## Boundaries and failures

- This is *contract inspection*, not the actual resolved input values, environment,
  Git facts, deferred Step outputs, or secret inspection. Do not display or infer
  runtime values from defaults alone.
- A Plan automatically exposes unambiguous, unbound direct public inputs. Optional
  `inputs:` restricts that interface; it never duplicates the owning Goon schema.
  Shared names with no explicit routing are omitted from caller inputs and retain
  independent defaults. Supplying one is an ambiguity error. Explicit `with:` forwarding
  and import-local `pass:` are traced only through the same analyzed graph:
  the latter compiles to identical `with:`-shaped `PlanInputBinding` edges.
  Mixed bindings preserve the same typed definitions and ultimate owner paths;
  inspecting a `pass:` binding does not trigger value resolution. Cycles, ambiguous projection,
  missing owners, invalid references, and unsafe source containment fail closed.
- Discovery is the deterministic exact `category/name` catalog. Inspection must
  use that public source identity, not a second index or README-derived schema.
- Inspection has no `RUN_ADMITTED`, Step, executor, Journal, or Game Tape side effect.
  Actual source admission, validation and execution remain the responsibility of
  their existing authorities. **Resolve once → transport once → execute once → report once.**

See [input resolution and transport](TEMPLATE_INPUT_RESOLUTION_TRANSPORT.md),
[catalog determinism](TEMPLATE_CATALOG_DETERMINISM.md), and
[fail before work](FAIL_BEFORE_WORK.md).

## Public contract discovery

`oh-goon plan inputs <plan.yml or goon.yml> [--json]` exposes this existing
inspection through one CLI presentation. It runs before constructing the
application runtime, never selects values, and cannot produce an admission
receipt. The versioned JSON response has `schema=oh-goon.plan-input-inspection/v1`,
`status=contract_inspected`, `inputs` with canonical owner paths, and
`root_source_sha256` identifying **only the root file**. The root digest is not
an imported-closure digest. When shared names need explicit routing, the optional
`unrouted_inputs` array lists those names in canonical source order. They are
excluded from accepted `inputs`; supplying one remains an ambiguity error.
The human report lists them even when there are no accepted inputs and suggests
`pass:` recipient selection or renamed `with:` bindings. Neither presentation
includes fixed binding values, environment values, or producer output bytes.
Fields that do not apply are omitted rather than
filled with invented owners, values, or empty authority claims.

`FleetPlanResolver.inspect_input_interface_with_source()` returns this richer
read-only interface and the root digest from one analyzed source cut. Existing
definition-only inspection methods remain projections of the same interface.
Ambiguity comes from canonical materialization; presentation does not infer
another set of input owners or resolve values.

After [development setup](../DEVELOPMENT.md), use the source CLI from the
repository root:

```sh
uv run --offline --frozen --no-sync --project backend oh-goon plan inputs \
  templates/plans/python/quality/plan.yml --json
```

## Resolved recipient routing

After discovering the contract with `plan inputs`, use `plan explain <plan.yml>
--routes [--json]` to explain **which actual import occurrence** receives
which root input, why, and which bindings instead carry workspace or deferred
output identity. This uses the same resolver/source cut; `pass:` and equivalent
`with:` have identical normalized routes. The opt-in machine schema is
`oh-goon.plan-input-routing/v1`, `status=resolved_not_executed` and never reports
caller input values or output bytes. It is deliberately separate from the
unchanged default `plan explain --json` schema.

The report preserves exact compiled import occurrence IDs, ordered repeats,
parent/nested identity, and explicit `as:` names. Contract `owner_paths`
describe possible schema owners; recipient routes describe active bindings
in the selected resolution, including conditional recipients. These are
different views of the same source graph.

Bindings distinguish parent input, producer workspace, deferred Goon output,
deferred Step output, external source, and redacted literal identity. They do
not reveal caller values, environment names, expression text, or output bytes.
A caller-file origin exposes only its contained relative path, digest, and
matching input names, using the existing capture rather than rereading the
file or changing precedence.

## Safe rejection diagnostics

`plan validate` and `plan explain` (including `--routes`) accept opt-in
`--diagnostics [--json]` to render failures using stable typed canonical
input-owner rejection codes. These presentations never include raw selected
input values, untrusted exception text or source files and do not attempt
resolution again after a failure. The machine schema is
`oh-goon.plan-validation-diagnostic/v1`. Only typed owner codes, safe syntactic
names, and a bounded correction action reach presentation; an arbitrary
exception yields a generic refusal. Source paths, expressions, and credentials
are not diagnostic text.

## Shared Plan API and editor

`POST /api/league/shared-plan-input-inspections` accepts the editor's exact
`plan_key`, `expected_revision`, and input dictionary. The existing
`SharedPlanService` resolves one pinned source cut through `FleetPlanService`.
A stale or missing saved Plan remains HTTP 409 or 404. Inspection creates no
lock, Run, background job, or admission receipt.

The response schema is `oh-goon.shared-plan-input-inspection/v1`.
`status=accepted` includes the validated Plan key, source SHA, and Plan ID;
actual Run still requires its original preview, lock, and admission checks.
`status=rejected` contains the same safe typed diagnostic projection used by
the CLI. The editor renders a report only for its current Plan, revision, and
input identity, invalidating it on edits so a stale success cannot authorize
execution.

## Evaluation and proof scope

The canonical-source HTTP journeys live in
`backend/tests/integration/api/test_t6_plan_input_journeys.py`; their corpus is
`backend/tests/integration/api/fixtures/t6_journeys.json`. They exercise the
shipped `python/quality`, `ml/iris-training`, and `kubernetes/set-image` Plans
through the real Shared Plan store, resolver, FastAPI routing, and TestClient.
They cover invalid input, correction, repeat identity, stale revision, malformed
input redaction, and absence of Run admission or effects.

From the initialized checkout:

```sh
uv run --offline --frozen --no-sync --project backend pytest -q \
  backend/tests/integration/api/test_t6_plan_input_journeys.py
npm --prefix frontend run test -- e2e/fixtures/BrowserFixtureApiClient.contract.test.ts
npm --prefix frontend run browser:test -- e2e/template-input-t6.spec.ts --project=chromium
```

The browser fixture checks presentation and redaction using scripted API
responses; it does not run the production resolver. Keep its result separate
from the canonical-source HTTP result and from native browser/environment
qualification. A missing browser, skipped case, or stalled TestClient is not
a pass. These deterministic journeys do not measure real-user productivity,
model behavior, Kubernetes effects, or release readiness. The broader
[authoring evaluation](../templates/TEMPLATE_UX_D_EVALUATION.md) retains those
measurement limits.
