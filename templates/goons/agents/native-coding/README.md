# agents/native-coding

Run one selected native coding agent over the captured repository and Skill.
The Agent owns reasoning, tools and editing within the selected isolated
workspace. Its report is a claim; native verification retains result authority.

**Status:** runnable with prerequisites. **Effects:** Lets the selected native agent edit its admitted isolated workspace and report the result.

**Prerequisites:** Installed Pi/OpenCode/Codex, a compatible isolated managed profile, captured repository/Skill sources and admitted write scope.

```sh
oh-goon templates init agents/native-coding --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** A typed native report. Project verification remains a separate executable Step.

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
