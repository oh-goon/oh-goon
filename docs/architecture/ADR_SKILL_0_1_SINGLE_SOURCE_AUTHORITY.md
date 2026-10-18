# ADR — SKILL-0 + SKILL-1: canonical Agent Skill source authority

**Status:** implemented for read-only authoring/prework only. **No Agent Step execution is enabled.**

## Why

Teams need an easy, committed, readable source registry for Agent Skills, analogous to `.gitmodules`. Existing OH-Goon already has the authoritative Plan/Goon graph, typed `skill_reference` inputs, the AG-01 fd-anchored source capture, AG-02 immutable Step handoff, and the original Fleet/Enforcer/LACE execution path. Creating a second parser, source downloader, harness runner or privileged Agent Step would duplicate authority and violate the one-owner rule.

## Frozen ownership

| Concern | Sole owner | Rule |
| --- | --- | --- |
| Plan/Goon semantics and imported Goon order | `FleetPlanResolver` / original source graph | No parallel Plan or agent parser |
| Project skill names | `.goonskills` next to authored root Plan/Goon | One explicit name → one local path; no parent discovery |
| Name expansion into source references | AG-01 `explain_agent_sources` | Registry supplies *only* alias entries; expansion remains at original owner |
| Skill source acquisition and full-resource bytes | `agent_authoring_sources._snapshot_authorized_tree` | Bounded, fd-anchored, regular-file capture including `SKILL.md`, references, scripts, assets |
| Agent Step identity and prework packet | `FleetPlanResolver.prepare_agent_step_handoff` / `FrozenAgentStepHandoff` | Original graph and captured bytes, immutable, explicitly no effect permission |
| Run/Step admission and capability decision | Enforcer | No `SKILL.md` or registry may grant authority |
| Containment, effect authorization, teardown | LACE / existing backend | Untrusted Agent Steps cannot use TL-1 |
| Step ordering and process command effects | Original Step executor | No new Agent runtime |
| Durable events and receipts | Original journal / Game Tape | No second audit stream |
| Repo intelligence | Hashmarks | Out of scope of this repository checkpoint |

## `.goonskills` v1 contract

```ini
[skill "team/review"]
path = skills/review
source = local
```

- Store `.goonskills` **beside the root authored Plan or `goon.yml`**, commit it with the Plan and referenced skill resources. `path` is relative to the **explicit authorized `skill_root`** supplied to source capture, not to an implicit working directory. For the example above, set `--skill-root` to the directory containing `skills/`.
- Valid skill names are lower-case, slash-separated labels (`review`, `team/review`) with hyphens. Named skill selection uses an existing `skill_reference` typed input. `local:<path>` works without registry as before.
- One file, maximum 64 KiB and 128 entries, UTF-8 and strict INI with exactly `path` and `source = local` per skill. Duplicate keys/sections, defaults, unknown fields, absolute or traversal paths, hidden paths, NUL/unsafe components, symlinks for the registry, and unsupported Git declarations fail before skill acquisition.
- No environment variable, user home search, parent traversal, `~` expansion, network fetch, chained aliases, shell interpretation, or implicit trust. When `.goonskills` is present, mixing it with explicit `--skill-alias` is rejected; the latter remains backward-compatible **only when the registry is absent**.
- The original captured source binds both the authored name and selected `local:` reference and the full captured member-byte identity. Retargeting a selected name changes the frozen capture identity; changes to unrelated registry entries do not redefine the selected source. A registry SHA-256 is available on the immutable read result for future provenance and diagnostics but is not a new Run authorization.
- `.goonskills` is **not** `.gitmodules`. Gitlink, verified commit pinning, remote acquisition and offline template archives are **SKILL-2** responsibilities. The `source = local` field must not be silently reinterpreted as a remote location.

## Read-only user path

The existing `oh-goon plan inputs ROOT --source-closure` uses `FleetPlanResolver.explain_agent_sources` and now automatically reads the adjacent `.goonskills` if it exists. `capture_agent_sources` and `prepare_agent_step_handoff` reuse the **same** named-reference resolution and original AG-01 byte reader. No application runtime, Fleet journal, LACE or process starts during source authoring inspection.

## Security and future acceptance

- `AgentPlanStep` remains authoring/prework only. `resolve()` still rejects Agent Steps before real execution. The new registry does not introduce an executable command or an agent harness adapter.
- TL-1 permits **operator-owned deterministic trusted code only**. It does not permit untrusted Agent Skills or model tool invocation. SKILL-3 needs original Agent Step admission, typed tool/egress capabilities, qualified containment and failure/cancellation proofs.
- SKILL-2 must retain repository and source identity and transport resources from exact Gitlink commits / offline bundles without alternative resolution. It can proceed independently of SKILL-3 admission engineering.
- Later agent loop SKILL-4 retains one sequential original sequencer. Reported `CLEAN` text is not a qualified result until bound to the current candidate and verification contract.
- Local native qualification is deferred until the real runtime path is ready. Hosted authoring tests are **not** native RUN-8 or a real Agent Step execution proof.

## Regression criteria

The test ring exercises named and namespaced resolution, original AG-01 member identity and AG-02 handoff parity, existing stateless CLI, legacy `local:` and explicit alias compatibility, malformed/duplicate registry fail-before-capture, symlink/oversize rejection, authority conflict, and Agent Step execution refusal. Unrelated Plan commands remain unchanged.
