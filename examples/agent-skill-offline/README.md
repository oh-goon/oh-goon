# Offline Skill resource fixture

Classification: preview-only-fixture

Verify that a selected Skill and its supporting files travel together through source capture. Its `opencode` declaration is preview-only and supplies no runnable native adapter.

Keep `.goonskills.yml`, `goon.yml` and the complete `skills/` tree together. The existing local registry maps `team/review` to its Skill directory. The checklist, script and JSON asset are transported resources: source capture never executes `scripts/verify.sh` merely because it is present.

The relocation regression is [`test_agent_skill_relocation.py`](../../backend/tests/unit/services/test_agent_skill_relocation.py). It checks complete source delivery using isolated fixtures. Offline capture/relocation proves neither model availability nor a completed Fleet review.

These tiny resources are product test data. Reusable Skills remain in [agentsCookbook](https://github.com/Hugloss/agentsCookbook); the [pinned-source example](../agent-skill-cookbook/README.md) uses the canonical Git registry and selected `run.yml` without adding a cookbook dependency to Oh-Goon. See [Agent Skills](../../docs/AGENT_SKILLS.md) for supported execution and qualification.
