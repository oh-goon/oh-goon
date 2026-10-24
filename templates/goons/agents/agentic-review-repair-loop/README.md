# agents/agentic-review-repair-loop

Compose four Skill reviews, adjudication, scoped repair and native project checks.

**Status:** runnable with prerequisites. **Effects:** Runs the declared model/tool/native phase with its declared repository access and disclosure recipient.

**Prerequisites:** Explicit repository/Skill source roots, a permitted acceptance profile and the configured provider/native agent required by this Goon. Project check scripts must exist when selected; effectful work still needs fresh original admission.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Typed Agent records, proposal data or native project check results, according to the selected phase. Agent claims do not become verification truth.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

The Atomic Goons own the input contracts. Required inputs are `repo`, `regression_command` and `verification_command`; `goal`, `acceptance`, `adjudication_skill` and `repair_skill` keep their defaults. Shared inputs use explicit `pass:` recipients, just like other Composite Goons.

Import from a verification Plan. Select registered Skills and a provider, then run with explicit review-pass, write-root and deadline limits. See [setup and execution](../../../../examples/agentic-review-repair-loop/README.md) for a complete invocation.

Reviews share one frozen candidate. Findings feed adjudication, repair and regression checks; each changed candidate restarts at reviewer one. Completion requires fresh CLEAN records from every reviewer and successful final verification on that candidate. Each phase uses the ordinary Run lifecycle; convergence does not confer APP certification.

Copy this Composite into a project-owned catalog to replace, append or reorder reviewers. Edit the ordered `phase: review` imports and their `with.skill` bindings. The controller accepts any registered Skill; native Git owns external Skill checkout and pinning.
