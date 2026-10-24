# python/secure

Run Python quality, then uv and Trivy vulnerability scanners.

**Status:** runnable with prerequisites. **Effects:** Runs native checks, makes declared online queries and retains the Trivy report.

**Prerequisites:** A committed `pyproject.toml` and native `uv.lock`; the selected group must supply Ruff, pytest and the chosen type checker. Prepare it with native `uv sync --locked --group dev` in the selected project. Native uv vulnerability audit support. Native Trivy and its declared online egress; configured strict content-store publication for the report. A missing scanner or unavailable network cannot establish a scan result. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init python/secure --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: python/secure}
target: {repository_id: REPOSITORY_ID}
inputs:
  project: .
  dependency_group: dev
  typechecker: ty
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** JUnit evidence, native uv audit output and retained `trivy-audit.json`. A nonzero scanner result fails its Step.

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.
