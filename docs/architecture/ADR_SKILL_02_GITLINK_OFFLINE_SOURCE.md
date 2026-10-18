# SKILL-2 — Gitlink-pinned source and single offline transport owner

**Decision:** extend the existing `.goonskills` source selector and its original
AG-01/AG-02 authority path. Do not create a Git skills downloader, script
runner, ZIP importer, skill-oriented executor, or second registry.

## Source declaration

```ini
[skill "team/review"]
source = gitlink
path = libs/agent-skills/team/review
gitlink = libs/agent-skills
revision = 0123456789abcdef0123456789abcdef01234567
```

`revision` is the **committed superproject Gitlink** SHA, not a mutable branch,
tag, URL or checkout-only revision. Gitlink resolution is explicitly offline:
`--skill-root` must point to the Git superproject; its HEAD tree and committed
`.gitmodules` must identify the exact Gitlink, and the submodule checkout HEAD
must match. No network fetch or implicit checkout is permitted. Mutable,
missing or untracked resources reject prework.

## One source byte owner and one transport

Git metadata is observed via the original `GitService` / original command
supervisor. The existing AG-01 fd-anchored snapshot reads SKILL.md and all
resources **once** under explicitly supplied roots. Each frozen resource is
checked against its pinned Git blob OID. This prevents clean-at-a-different-
commit and dirty-content ambiguity without another source reader. Gitlink
revision is carried as part of the resolved source reference and committed into
the existing AG-02 prework capture identity.

The **original** AG-02 `encode_prework_delivery` and
`decode_prework_delivery` serialize and verify every member byte and its hash;
there is no second offline ZIP or template materializer. A relocated local
example at `examples/agent-skill-offline/` uses the same resolver path with no
Git dependency. An uninitialized Gitlink is a refusal, not a clone request.

## Authority and negative cases

A skill source cannot grant effects. `agent_authoring_sources` remains
preview-only and the original resolver rejects runnable Agent Steps. Strict
Enforcer, LACE and Game Tape ownership is untouched. Untrusted Agent Skills
cannot use the operator-owned trusted-local TL-1 exception. SKILL-3 will
require a separately admitted, qualified contained backend and explicit tool,
write and model egress authorization. No dependency/lockfile changes.

Test matrix: pinned Gitlink; complete SKILL.md/scripts/assets/references;
content identity; offline relocation and no-checkout packet decoding; wrong
revision; missing Gitlink; untracked/changed/missing worktree; mismatched
checkout HEAD; invalid paths/aliases; byte-vs-Git-tree adversarial check;
original CLI and non-executable Agent Step preservation.
