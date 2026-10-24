# agents/agentic-review-repair-loop

Run four ordered reviews, adjudication, bounded repair, regression and final verification.

**Status:** runnable with prerequisites. **Effects:** Sends admitted repository evidence to the configured model; admitted repair can edit explicit write roots.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Prepare the pinned Skill registry and explicit executable project check scripts. Repair requires explicit write roots and fresh Run admission.

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init agents/agentic-review-repair-loop --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: agents/agentic-review-repair-loop}
target: {repository_id: REPOSITORY_ID}
inputs:
  repo: local:repo
  regression_command: scripts/regression
  verification_command: scripts/verify
agent:
  model_profile: local
  acceptance: agentic-review
  repository_root: /approved/repositories
  skill_root: .
repair:
  review_passes: 5
  write_roots: [src, tests]
  timeout_seconds: 1800
  step_timeout_seconds: 300
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml --authorize-repair
```

**Expected outputs:** Linked Run/candidate records, native regression results and final verification; model CLEAN/repair claims alone do not prove success.

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
