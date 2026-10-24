# python/uv-audit

Audit the repository-root uv lock with native uv vulnerability audit.

**Status:** runnable with prerequisites. **Effects:** Reads the lock and makes the declared online vulnerability queries.

**Prerequisites:** Native uv supporting `uv --preview audit`, a repository-root uv lock and admitted `host_unrestricted` egress. No dependency group is selected by this Goon.

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init python/uv-audit --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: python/uv-audit}
target: {repository_id: REPOSITORY_ID}
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** Native JSON audit output and Step status. Missing uv/network and vulnerability findings retain their actual outcomes.

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.
