# ADR — AGENT-01B3F frozen Skill-resource inspection

## Context

The original AG-01 Resolver captures `SKILL.md` and supporting files and AG-02
transports those immutable bytes. Earlier Fleet harnesses disclosed only the
SKILL.md instructions to the selected model, and optionally read repository
members. Real Agent Skills frequently depend on captured `references/` and other
resources. Reopening the authored Skill checkout would violate the frozen-source
contract.

## Decision

Expose **one distinct opt-in harness** `local-model-skill-resources`. Reuse the
same selected Ollama recipient, model parser, original Fleet attempt, original
Step supervisor, frozen source preparer, Game Tape and immutable AG-02 handoff.
Extend the existing strict tool-action contract with:

- `list_skill_paths`: native `rg --files --hidden --no-ignore -- skill` on frozen Skill members;
- `read_file` path `skill/<exact-captured-member>`: existing `head -c 2048 -- ...`;
- unchanged `list_paths`, `search_text`, and `read_file` for `repo/` members.

The source capture owns the set of valid paths and content hashes. The existing
Fleet Step owner decides whether and how to execute a selected action. No
second resolver, process runner, journal or Hashmarks repository-identity
adapter is permitted. `SKILL.md` is model input, not authority. A model reply
cannot add tools, read arbitrary host paths, grant permissions, or change the
two-tool budget. Model summaries remain untrusted text, not Run proof.

## Backward compatibility

Every older harness rejects `list_skill_paths` and `skill/...` read_file, even
when it can inspect `repo/...`; only the new selected harness allows both.
The new invocation stays offline except for the pre-existing operator-selected
local Ollama endpoint; no remote provider or repository writes.

## Verification and open qualification

Real supervised `rg` and `head` commands are tested against the frozen Skill
capture through original Fleet admission and persisted Game Tape. Denied
paths/actions are rejected before tool dispatch. The fixture simulates model
replies; real installed Ollama, pinned Hashmarks, native Linux/WSL and full
project verification must be qualified separately. This ADR does not make
advanced high-security host gates mandatory for trusted standard execution.
