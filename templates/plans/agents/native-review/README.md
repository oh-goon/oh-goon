# agents/native-review

Run one typed read-only native review using a selected Skill.

**Status:** runnable with prerequisites. **Effects:** Reads the captured repository and selected Skill; discloses admitted evidence to the native model recipient.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Install Codex CLI and configure a compatible isolated managed profile with a Responses connection; see [native configuration](../../../../docs/AGENT_PROVIDERS.md#run-a-native-coding-agent). Native model/auth settings remain native-owned.

```sh
oh-goon templates init agents/native-review --kind plan --output ./workflow
```

The initializer selects Codex. Edit `workflow/run.yml` with your registered target and caller choices; select Pi or OpenCode explicitly with a compatible profile. Omitted options keep their Goon-owned defaults:

```yaml
version: 1
source: {kind: catalog-plan, name: agents/native-review}
target: {repository_id: REPOSITORY_ID}
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
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** A typed review result; findings/incomplete status grant no write or verification authority.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
