# agents/review

Review one frozen candidate using a caller-selected registered Skill.

**Status:** runnable with prerequisites. **Effects:** Reads the captured repository and Skill, and sends admitted evidence to the configured provider.

**Prerequisites:** Captured repository/Skill sources, an admitted acceptance profile and a configured model provider.

```sh
oh-goon init --template agents/review --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** Typed findings with their original evidence and scope limits.

Read-only Agent. `repo`, `skill`, `goal` and `acceptance` select the captured source and task. The invocation selects a provider profile; the Skill grants no effects. Returns a typed candidate-, scope- and Skill-bound record. Import this Goon repeatedly with different Skills and occurrence aliases.

For a single review, import this Goon in a verification Plan, bind `skill` and select source roots, acceptance and provider profile in the same `run.yml` used for `workflow prepare` and `run`. Loop limits are needed only when the Plan declares phases. See [Agent Skills](../../../../docs/AGENT_SKILLS.md#native-agentic-reviewrepair-loop).
