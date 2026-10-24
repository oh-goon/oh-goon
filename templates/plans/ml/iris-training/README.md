# ml/iris-training

Train and evaluate through a repository-provided `iris-train` command.

**Status:** teaching example. **Effects:** Executes training and writes metrics and model parameters.

**Prerequisites:** The project must implement `iris-train` in its locked `ml` group. The existing template-certification Iris fixture is the reference workload; this template does not install a training program. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

**What to edit:** initialize a workflow, then set the registered target and relevant caller choices in `workflow/run.yml`. The illustrative addresses, revision and digest below must identify your real workload before execution. The [first-run guide](../../../README.md) explains repository registration and prepared CLI use.

**Settings owner:** [`plan.yml`](plan.yml) owns ordered imports; imported Goons own input types/defaults, Steps, effects and outputs. `run.yml` selects values. Native project tools keep their own configuration.

```sh
oh-goon templates init ml/iris-training --output ./workflow
```

Edit the generated `workflow/run.yml`; this shows one explicit selection:

```yaml
version: 1
source: {kind: catalog-plan, name: ml/iris-training}
target: {repository_id: REPOSITORY_ID}
inputs:
  random_seed: 42
  test_size_percent: 20
  minimum_accuracy_percent: 90
  max_iterations: 300
```

```sh
oh-goon config inspect ./workflow/run.yml
oh-goon config validate ./workflow/run.yml
oh-goon lock ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

**Expected outputs:** `ml-output/iris-training-metrics.json` is evidence; `ml-output/iris-model-parameters.json` has retained content-store bytes. Native command success determines acceptance.

**Failures:** invalid inputs fail before work. Inspection/validation do not prove installed tools, reachable services or successful effects. Native failures remain failed Steps with their exit status and logs. A changed source or execution selection requires a new lock.

**Customize:** change caller values in `run.yml`. For structural changes, create a project Plan with ordinary ordered `goons:` imports and select it with `source: {kind: file-plan, path: plan.yml}`; see [Plan authoring](../../README.md). Keep secrets in declared execution environment bindings.
