# agents/native-coding

Run one writable native Agent, then the project’s explicit verification script.

**Status:** runnable with prerequisites. **Effects:** Sends admitted evidence to the native agent and permits scoped writes before native verification.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Install the selected native agent and configure a compatible isolated managed profile; native model/auth settings remain native-owned.

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init agents/native-coding --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: agents/native-coding}
target: {repository_id: REPOSITORY_ID}
inputs:
  repo: local:repo
  skill: local:skills/repair
  verification_command: scripts/verify
agent:
  implementation: pi
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

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
