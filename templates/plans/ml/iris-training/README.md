# ml/iris-training

Train and evaluate through a repository-provided `iris-train` command.

**Status:** teaching example. **Effects:** Executes training and writes metrics and model parameters.

**Prerequisites:** The project must implement `iris-train` in its locked `ml` group and produce the declared metrics and model-parameter files. The template-certification Iris fixture checks command arguments and startup/admission only; it does not train a model or produce those artifacts. Configure the admitted strict executor first; see the [execution contract](../../../../docs/CI_CD_CONTRACT.md).

```sh
oh-goon templates init ml/iris-training --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted options keep their Goon-owned defaults:

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
