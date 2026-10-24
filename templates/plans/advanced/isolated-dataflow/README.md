# advanced/isolated-dataflow

Select project preparation, tests, build and publication scripts with explicit output/environment transfer.

**Status:** teaching example. **Effects:** Runs project commands and can publish through `ci/publish.sh`.

**Prerequisites:** Project-owned `ci/prepare.sh` and `ci/publish.sh`, native uv with pytest/pytest-timeout, build prerequisites and explicitly admitted `RELEASE_TOKEN`. Read the scripts before using them.

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init advanced/isolated-dataflow --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: advanced/isolated-dataflow}
target: {repository_id: REPOSITORY_ID}
inputs:
  tag: dev
  test_timeout_seconds: 600
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `RELEASE_TAG` from the prepare Step and native Step outcomes; project scripts own other products.

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.
