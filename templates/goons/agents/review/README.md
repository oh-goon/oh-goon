# agents/review

Review one frozen candidate using a caller-selected registered Skill.

Read-only Agent. `repo`, `skill`, `goal` and `acceptance` select the captured source and task. The invocation selects a provider profile; the Skill grants no effects. Returns a typed candidate-, scope- and Skill-bound record. Import this Goon repeatedly with different Skills and occurrence aliases.

For a single review, import this Goon in a verification Plan, bind `skill` and run with the same source roots, acceptance and provider flags used for `lock`. Loop limits are needed only when the Plan declares phases. See [Agent Skills](../../../../docs/AGENT_SKILLS.md#native-agentic-reviewrepair-loop).
