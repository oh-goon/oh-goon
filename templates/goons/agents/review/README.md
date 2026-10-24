# agents/review

Review one frozen candidate using a caller-selected registered Skill.

**Status:** runnable with prerequisites. **Effects:** Reads the captured repository and Skill, and sends admitted evidence to the configured provider.

**Prerequisites:** Captured repository/Skill sources, an admitted acceptance profile and a configured model provider.

```sh
oh-goon templates init agents/review --kind goon --output ./workflow
```

Set `target.repository_id` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** Typed findings with their original evidence and scope limits.

Read-only Agent. `repo`, `skill`, `goal` and `acceptance` select the captured source and task. The invocation selects a provider profile; the Skill grants no effects. Returns a typed candidate-, scope- and Skill-bound record. Import this Goon repeatedly with different Skills and occurrence aliases.

For a single review, import this Goon in a verification Plan, bind `skill` and run with the same source roots, acceptance and provider flags used for `lock`. Loop limits are needed only when the Plan declares phases. See [Agent Skills](../../../../docs/AGENT_SKILLS.md#native-agentic-reviewrepair-loop).
