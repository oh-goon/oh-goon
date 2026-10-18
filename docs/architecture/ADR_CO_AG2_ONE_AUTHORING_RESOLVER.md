# ADR — CO-AG2: one owner for read-only authoring

**Decision.** Resolve and inspect Plan authoring in `FleetPlanResolver`. Route CLI `explain`, `explain --routes`, `check`, and `diff` through that resolver before ApplicationRuntime construction. Maintain service/API compatibility by delegating `FleetPlanService.check` and `.diff` to the resolver. Continue using `check_plan_sources`, `semantic_plan_diff`, and input-routing projection without implementing replacements.

**Boundary.** These are diagnostic *reads*, not admission, locking, preparing tool environments, execution or proof of containment. The commands never create the application runtime. `plan run` retains all original CLI/service enrollment fences, Enforcer and LACE; `prepare` remains effectful and untouched. `preview` still depends on a repository and is not reclassified by implication.

**Compatibility.** Preserve the existing `FleetPlanCheckReport`, `FleetPlanResolutionReport`, `FleetPlanSemanticDiff`, routing JSON schema, diagnostics, human output and exit codes. Directory `check` remains syntax-only as before, while file `check` validates with the canonical resolver. Read-only comparison resolves each input once per compared Plan, as before.

**Safety.** No new Hashmarks dependency, semantic authority, output/diagnostic owner, retry policy, lock write or signer. No native gate is loosened. A focused regression prevents SQLite/Fleet construction for each diagnostic and checks the shared resolver result.
