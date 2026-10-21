# agents/native-review

Review a frozen candidate with a selected native agent and caller-selected Skill.
The workspace is physically read-only. The existing typed review acceptor checks
candidate, scope, Skill and occurrence, including complete scope accounting for
completed reviews. A native completion event cannot substitute for that record.

`repo` and `skill` select the authorized captured sources. `goal` defaults to
following the Skill; `acceptance` defaults to `agentic-review` and must be
explicitly allowed by the caller. `configured_model` binds to the selected
managed inference recipient before capture.

Use the existing `--native-agent`, `--native-profile`, `--native-workspace` and
`--agent-providers-file` flags with the same source roots, acceptance and inputs
for lock and Plan execution. Native reasoning requires an isolated Bubblewrap
profile and managed inference; the default `isolated-managed` profile consumes
the catalog's explicit `default_model`. See [native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent)
and the [single review Plan](../../../plans/agents/native-review/README.md).

This Goon is one review occurrence. Existing phased campaigns retain their
original controller and budgets; this package introduces no repair loop.
