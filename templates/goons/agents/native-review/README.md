# agents/native-review

Review a frozen candidate with a selected native agent and caller-selected Skill.
The workspace is physically read-only. The existing typed review acceptor checks
candidate, scope, Skill and occurrence, including complete scope accounting for
completed reviews. A native completion event cannot substitute for that record.

**Status:** runnable with prerequisites. **Effects:** Runs the declared model/tool/native phase with its declared repository access and disclosure recipient.

**Prerequisites:** Explicit repository/Skill source roots, a permitted acceptance profile and the configured provider/native agent required by this Goon. Project check scripts must exist when selected; effectful work still needs fresh original admission.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Typed Agent records, proposal data or native project check results, according to the selected phase. Agent claims do not become verification truth.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

`repo` and `skill` select the authorized captured sources. `goal` defaults to
following the Skill; `acceptance` defaults to `agentic-review` and must be
explicitly allowed by the caller. `configured_model` binds to the selected
managed inference recipient before capture.

Select `agent.implementation` and `agent.profile` in `run.yml`. Native reasoning requires an isolated Bubblewrap
profile and managed inference; the default `isolated-managed` profile consumes
the catalog's explicit `default_model`. See [native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent)
and the [single review Plan](../../../plans/agents/native-review/README.md).

This Goon is one review occurrence. Existing phased campaigns retain their
original controller and budgets; this package introduces no repair loop.
