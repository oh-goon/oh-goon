# agents/agentic-review-repair-loop

Run four ordered reviews, adjudication, bounded repair, regression and final verification.

**Status:** runnable with prerequisites. **Effects:** Sends admitted repository evidence to the configured model; admitted repair can edit explicit write roots.

**Prerequisites:** An authorized repository root, captured Skill resources and configured model connection. Prepare the pinned Skill registry and explicit executable project check scripts. Repair requires explicit write roots and fresh Run admission.

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind plan --output ./workflow --draft
```

This explicit draft is NOT_READY. Set `target.repository` to your exact registered repository path and fill the required caller choices in `workflow/run.yml`. Omitted options keep their Goon-owned defaults:

```yaml
version: 2
source: {kind: catalog-plan, name: agents/agentic-review-repair-loop}
target: {repository: /absolute/path/to/project}
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
oh-goon workflow inspect ./workflow/run.yml
oh-goon workflow validate ./workflow/run.yml
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml --authorize-repair
```

**Expected outputs:** Linked Run/candidate records, native regression results and final verification; model CLEAN/repair claims alone do not prove success.

The [Agent example](../../../../examples/agentic-review-repair-loop/README.md) explains Skill capture and bounded repair. A stored selection never grants fresh write consent.
