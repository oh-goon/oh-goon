# Frozen local-search fixture

Classification: integration-fixture

This small product example demonstrates the existing Resolver capture and Step
preparation contract. Its one-sentence `SKILL.md` is test data. Reusable review
and development Skills are owned by
[agentsCookbook](https://github.com/Hugloss/agentsCookbook); see the
[optional pinned-source recipe](../agent-skill-cookbook/README.md).

The `local-search` harness and `search:needle` task use
`prepare_frozen_readonly_skill_search(handoff)`. That function stages verified
frozen repository and Skill bytes and prepares one fixed `rg --fixed-strings`
command. Preparation does not execute a command. The original Step executor
owns execution, timeouts, cancellation, and output; Fleet owns Run admission
and durable results when that Step is run through Fleet.

The related functional tests construct isolated source fixtures and exercise
this same preparation/Step boundary:

```sh
uv run --project backend --offline --frozen --no-sync pytest backend/tests/integration/test_agent01a_frozen_local_search.py
```

Run that command from the OH-Goon root with native `rg` available. It tests a
local tool turn without invoking a model. Supported model harnesses, Fleet
execution, and their remaining qualification limits are documented in
[Agent Skills](../../docs/AGENT_SKILLS.md).
