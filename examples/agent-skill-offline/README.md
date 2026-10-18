# Offline Skill resource fixture

This product fixture checks that a selected Skill and its supporting files travel
together through source capture. Its tiny local review text, checklist, script,
and asset are test data. Reusable review/development Skills remain in
[agentsCookbook](https://github.com/Hugloss/agentsCookbook); the
[optional Gitlink recipe](../agent-skill-cookbook/README.md) selects a real upstream
Skill without adding a cookbook dependency to OH-Goon.

Copy this directory in full, including `.goonskills`, `goon.yml`, and all files
under `skills/`. The name `team/review` resolves through the existing registry
and source-capture owner. The relocation regression in
[`test_skill02_pinned_gitlink.py`](../../backend/tests/unit/services/test_skill02_pinned_gitlink.py)
uses this complete fixture.

Use `oh-goon plan inputs goon.yml --source-closure` with the exact
`--expect-sha256` from ordinary `plan inputs`, explicit `--repository-root`,
`--skill-root .`, `--allow-acceptance review`, and these inputs:

- `--input repo=local:PROJECT`
- `--input skill=team/review`
- `--input acceptance=review`

Capture and immutable delivery work offline. The included `scripts/verify.sh`
is transported as a resource; selecting the Skill does not execute that script.
The fixture's `opencode` Goon is preview-only. It is not an installed OpenCode
adapter or a prerequisite for ordinary Agent execution. See
[Agent Skills](../../docs/AGENT_SKILLS.md) for the supported local harnesses and
their existing Fleet/Step execution path.
