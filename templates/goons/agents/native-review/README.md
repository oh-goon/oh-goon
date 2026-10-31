# agents/native-review

Review a frozen candidate with a selected native agent and caller-selected Skill.
The workspace is physically read-only. The existing typed review acceptor checks
candidate, scope, Skill and occurrence, including complete scope accounting for
completed reviews. A native completion event cannot substitute for that record.

**Status:** runnable with prerequisites. **Effects:** Reads captured repository/Skill sources and sends admitted evidence to the selected native model recipient.

**Prerequisites:** Installed Pi/OpenCode/Codex, a compatible isolated managed profile, captured repository/Skill sources and an admitted acceptance profile.

```sh
oh-goon init --template agents/native-review --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** A typed native review record. Findings do not establish verification success.

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
