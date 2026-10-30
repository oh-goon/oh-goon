# Review with a pinned agentsCookbook Skill

Classification: runnable-with-prerequisites

Run a typed read-only review using the existing `agents/native-review` Plan and the upstream `codebase-finding-derivation` Skill. Admitted repository evidence is disclosed to the selected native model recipient. A review result does not grant write authority or establish passing verification.

**Prerequisites:** an initialized Oh-Goon installation, registered workload repository, installed Codex CLI and a compatible isolated managed profile with a Responses connection in the provider catalog. Native sign-in, model settings, context window and compaction stay native-owned. Remote Git source capture may need network/cache access; ordinary inspection does not fetch it.

Initialize a separate workflow, then copy the included `.goonskills.yml` into it:

```sh
oh-goon init --template agents/native-review --kind plan --output ./workflow --draft
```

Edit `workflow/run.yml` using [the complete selection](run.yml). Set the real `target.repository`, `inputs.repo`, approved `agent.repository_root` and selected profile. The example follows the initializer’s Codex default; select Pi or OpenCode explicitly with a compatible profile. Keep `agent.skill_root: .` and the registry beside `run.yml`. The registry owns the Git source/ref/directories once; do not copy these facts into the run selection:

```yaml
sources:
  cookbook:
    source: https://github.com/Hugloss/agentsCookbook.git
    ref: 40be826e6883d819ee6cf5c10946c793f0ef0f5f
    skills:
      - skills/codebase-finding-derivation
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

Lock captures the exact remote commit and Skill closure in `goon.lock`; normal execution reuses that identity. Changing the selected source/profile requires explicit new lock authority. No cookbook checkout is needed to build or test Oh-Goon.

Expected output is the native typed review record. Findings, incomplete scope, unavailable agent/provider and failed admission retain their real outcomes. Inspection/validation alone cannot prove a completed review. Select a different registered Skill through `inputs.skill`; retain original effect and acceptance admission. See [Agent Skills](../../docs/AGENT_SKILLS.md) and [provider configuration](../../docs/AGENT_PROVIDERS.md).
