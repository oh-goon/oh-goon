# ADR — CO-AG3: canonical repository preview without Fleet startup

## Context

`plan preview` is repository-specific, unlike simple Plan validation: it checks import applicability and filesystem-dependent step conditions. The CLI previously built a full `ApplicationRuntime` to reach `FleetPlanService.preview`, even though that service only resolved a Plan and called the existing pure `preview_plan(repository, plan)` evaluator.

## Decision

Provide `FleetPlanResolver.preview(plan_path, repository, extensions_path, inputs)`, delegating to its existing `resolve` method and the original repository evaluator `integrations.fleet_plan_preview.preview_plan`. The existing `FleetPlanService.preview` delegates to this operation; `FleetPlanService.preview_resolved` remains available for existing shared-plan callers and retains the same evaluator. The CLI selects the resolver before ApplicationRuntime construction for `plan preview` only.

## Authority and freshness

Preview is a time-of-observation diagnostic. Repository conditions may change after the call. No preview disposition grants execution permission, produces an authenticated source binding, or changes the Run admission/source currentness checks. Native effects and execution still go through the original Enforcer/Fleet/LACE chain.

## Compatibility

Preserve the exact `FleetPlanPreview` JSON and text representations, repository path identity, original argument binding, error handling, and return codes. Reuse step/disposition reasoning; do not create a second decision owner. `plan run`, `plan prepare`, shared-plan preview, and HTTP lifecycle remain unchanged. No Hashmarks dependency or lockfile change.

## Regression gate

Regression tests forbid runtime construction, compare projection parity for shipped templates, check import applicability and invalid Plan/input refusal, prove no lock/database mutation, and assert one resolver delegation from the Plan service. Fresh source extraction must pass tests, architecture, and self-excluding manifest audit.
