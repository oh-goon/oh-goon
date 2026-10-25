# agents/native-coding

Run one writable native Agent, then the project’s explicit verification script.

**Status:** runnable with prerequisites. **Effects:** Sends admitted evidence to the native agent and permits scoped writes before native verification.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Install Codex CLI and configure a compatible isolated managed profile with a Responses connection; see [native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent). Native model/auth settings remain native-owned.

```sh
oh-goon templates init agents/native-coding --kind plan --output ./workflow
```

The initializer selects Codex. Edit `workflow/run.yml` with your registered target and caller choices; select Pi or OpenCode explicitly with a compatible profile. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: agents/native-coding}
target: {repository_id: REPOSITORY_ID}
inputs:
  repo: local:repo
  skill: local:skills/repair
  verification_command: scripts/verify
agent:
  implementation: codex
  profile: isolated-managed
  acceptance: native-coding
  repository_root: /approved/repositories
  skill_root: .
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Native Agent records and actual verification Step outcomes.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
