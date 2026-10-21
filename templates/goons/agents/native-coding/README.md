# agents/native-coding

Run one selected native coding agent over the captured repository and Skill.
The Agent owns reasoning, tools and editing within the selected isolated
workspace. Its report is a claim; native verification retains result authority.

`repo` and `skill` select the authorized captured sources. `goal` defaults to
following the Skill; `acceptance` defaults to `native-coding` and must be
explicitly allowed by the caller. `configured_model` binds to the selected
managed inference recipient before capture.

Select Pi, OpenCode or Codex with the existing native Plan/lock flags:
`--native-agent`, `--native-profile`, `--native-workspace` and
`--agent-providers-file`. Use the same source roots, acceptance and inputs when
locking and running. Native reasoning requires a Bubblewrap profile and a
managed inference connection. The default `isolated-managed` profile uses the
catalog's explicit `default_model`; explicit `local` does not support native
reasoning. See [provider and native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent).

The [native coding Plan](../../../plans/agents/native-coding/README.md) follows
this Agent with the project's selected verification executable. No model or
agent executable is hardcoded in this package.
