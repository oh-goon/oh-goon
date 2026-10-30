# agents/native-coding

Run one writable native Agent, then the project’s explicit verification script.

**Status:** runnable with prerequisites. **Effects:** Sends admitted evidence to the native agent and permits scoped writes before native verification.

**Prerequisites:** An authorized repository root, captured Skill resources and an installed native agent with an explicit compatible profile. For trusted local Codex coding, use the [existing sign-in profile](../../../../docs/AGENT_PROVIDERS.md#use-an-existing-codex-sign-in). Codex owns authentication; no auth-file or env-file inspection is needed. Read-only reviews retain their isolation requirements.

```sh
oh-goon init --template agents/native-coding --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

The initializer selects Codex with `isolated-managed`. Edit `workflow/run.yml` with your registered target and caller choices; the example below explicitly selects your configured `signed-in` profile for trusted local coding. Select Pi or OpenCode explicitly with a compatible profile. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: agents/native-coding}
target: {repository: /absolute/path/to/project}
inputs:
  repo: local:repo
  skill: local:skills/repair
  verification_command: scripts/verify
agent:
  implementation: codex
  profile: signed-in
  acceptance: native-coding
  repository_root: /approved/repositories
  skill_root: .
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Native Agent records and actual verification Step outcomes.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
