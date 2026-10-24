# Local Skill authoring fixture

Classification: preview-only-fixture

Demonstrate typed inputs and local Skill source capture. The `opencode` harness declaration is preview-only; it neither installs OpenCode nor supplies a runnable native adapter.

The fixture consists of `.goonskills.yml`, `goon.yml` and the entire `skills/` resource tree. Keep these files together when relocating it. The registry’s `review` alias selects the local resource; source capture owns the member bytes and identity. Selecting a Skill does not execute its text or resources.

This fixture’s `SKILL.md` is product test data. Reusable development/review Skills belong to [agentsCookbook](https://github.com/Hugloss/agentsCookbook). Use the [pinned cookbook review](../agent-skill-cookbook/README.md) for a supported native workflow with one selected `run.yml`.

Preview/source capture starts no model, tool Step or Fleet Run and proves no review result. See [Agent Skills](../../docs/AGENT_SKILLS.md) for source capture, permissions and native qualification boundaries.
