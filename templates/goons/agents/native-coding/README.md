# agents/native-coding

Run one selected native coding agent over the captured repository and Skill.
The Agent owns reasoning, tools and editing within the selected isolated
workspace. Its report is a claim; native verification retains result authority.

**Status:** runnable with prerequisites. **Effects:** Runs the declared model/tool/native phase with its declared repository access and disclosure recipient.

**Prerequisites:** Explicit repository/Skill source roots, a permitted acceptance profile and the configured provider/native agent required by this Goon. Project check scripts must exist when selected; effectful work still needs fresh original admission.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Typed Agent records, proposal data or native project check results, according to the selected phase. Agent claims do not become verification truth.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

`repo` and `skill` select the authorized captured sources. `goal` defaults to
following the Skill; `acceptance` defaults to `native-coding` and must be
explicitly allowed by the caller. `configured_model` binds to the selected
managed inference recipient before capture.

Select Pi, OpenCode or Codex with `agent.implementation` and `agent.profile` in `run.yml`. Native reasoning requires a Bubblewrap profile and a
managed inference connection. The default `isolated-managed` profile uses the
catalog's explicit `default_model`; explicit `local` does not support native
reasoning. See [provider and native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent).

The [native coding Plan](../../../plans/agents/native-coding/README.md) follows
this Agent with the project's selected verification executable. No model or
agent executable is hardcoded in this package.
