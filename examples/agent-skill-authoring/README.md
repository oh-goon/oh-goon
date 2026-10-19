# Local Skill authoring fixture

This small product fixture demonstrates typed inputs and local Skill source
capture. Its `SKILL.md` is sample data, not a reusable review Skill catalog.
Reusable review and development Skills are owned by
[agentsCookbook](https://github.com/Hugloss/agentsCookbook); see the
[optional pinned-source recipe](../agent-skill-cookbook/README.md).

Commit `.goonskills.yml`, `goon.yml`, and the entire `skills/` resource tree together.
The registry path is relative to the explicitly supplied `--skill-root`, which
is this example directory. `--repository-root` selects a separate directory
containing the repository the caller authorizes for inspection.

`oh-goon plan inputs examples/agent-skill-authoring/goon.yml` reports the typed
inputs and root source SHA-256. Supply that digest with `--source-closure`,
`--expect-sha256 SHA`, `--repository-root ROOT`,
`--skill-root examples/agent-skill-authoring`, `--allow-acceptance review`,
`--input repo=local:PROJECT`, `--input skill=review`, and
`--input acceptance=review` to inspect the captured members and their identity.

The `opencode` harness in this fixture is an authoring-only example. It does not
install OpenCode or provide an executable OpenCode integration. Supported
local Agent harnesses and ordinary execution options are documented in
[Agent Skills](../../docs/AGENT_SKILLS.md). Source inspection itself starts no
model, tool Step, or Fleet Run.
