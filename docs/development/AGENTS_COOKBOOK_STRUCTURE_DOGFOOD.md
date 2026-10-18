# agentsCookbook boundary

For contributors working on Oh-Goon, agentsCookbook is an **external development-only aid**.
It owns reusable review Skills and developer-side probe methodology. Keep that
methodology upstream; Oh-Goon's local instructions define its owners, product
contracts, and commands.

The reviewed upstream commit is recorded in
[`agents-cookbook-source.json`](agents-cookbook-source.json). Load the canonical
Skills directly when useful:

- [Finding derivation](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/codebase-finding-derivation/SKILL.md).
- [Behavioral coverage review](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/coverage-design-review/SKILL.md).
- [Full Skill catalog](https://github.com/Hugloss/agentsCookbook/blob/40be826e6883d819ee6cf5c10946c793f0ef0f5f/skills/README.md).

Review current source. Do **not** carry a previous hotspot or filename forward as a predetermined target.
Oh-Goon's own verification and certification gates remain authoritative;
cookbook findings and review receipts do not grant edits, execution, or PASS.

Hashmarks owns the repository-intelligence contracts consumed by Oh-Goon.
Oh-Goon owns admission, execution, result publication, and its product invariants.
Fix a reusable Skill or probe defect in agentsCookbook, a repository-intelligence
defect in Hashmarks, and an Oh-Goon defect in its existing product owner.

The cookbook is neither a package dependency nor vendored supply. Optional
developer checkouts stay outside packageable source or in ignored local state.
Build, test, Run, and release operations must work without that checkout.

A caller may also select cookbook Markdown as workload input through the
existing [Agent Skill source contract](../AGENT_SKILLS.md). The
[Gitlink example](../../examples/agent-skill-cookbook/README.md) pins resources
in the caller's workflow repository. That selection creates no cookbook loader,
runtime dependency, implicit network fetch, or permission from Skill text.
