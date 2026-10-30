# agents/native-review

Run one typed read-only native review using a selected Skill.

**Status:** runnable with prerequisites. **Effects:** Reads the captured repository and selected Skill; discloses admitted evidence to the native model recipient.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Install Codex CLI and configure a compatible isolated managed profile with a Responses connection; see [native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent). Native model/auth settings remain native-owned.

```sh
oh-goon init --template agents/native-review --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

The draft selects Codex. Complete `workflow/run.yml` with your exact registered target path and caller choices; select Pi or OpenCode explicitly with a compatible profile. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: agents/native-review}
target: {repository: /absolute/path/to/project}
inputs:
  repo: local:repo
  skill: local:skills/single-source-of-truth-review
agent:
  implementation: codex
  profile: isolated-managed
  acceptance: agentic-review
  repository_root: /approved/repositories
  skill_root: .
```

```sh
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** A typed review result; findings/incomplete status grant no write or verification authority.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
