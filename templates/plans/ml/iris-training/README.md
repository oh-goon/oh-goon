# ml/iris-training

Train and evaluate using the complete Python project in
[ml/iris-train](../../../goons/ml/iris-train/README.md).

**Status:** teaching example. **Effects:** Executes training and writes metrics
and model parameters. **Prerequisites:** Python 3.12+, native uv, an admitted
strict executor and content-store publication. See the [execution contract](../../../../docs/CI_CD_CONTRACT.md).
The target repository needs no Iris implementation or `ml` dependency group.

```sh
oh-goon templates init ml/iris-training --kind plan --output ./workflow
```

Edit `workflow/run.yml` with your registered target and caller choices. Omitted
values retain their Goon-owned defaults:

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

The Plan stays small: `purpose: cicd` and the ordered Goon import. The Goon's
project owns Python dependencies, build configuration, installed command and
tests. Native uv installs its base runtime dependencies in an owned execution
environment, leaving developer environments alone.
The Plan retains its default trusted-code policy. Explicit
`trust_repository_code: false` refuses package preparation before build;
isolated builds for untrusted Python packages are unavailable.

**Expected outputs:** `ml-output/iris-training-metrics.json` is evidence;
`ml-output/iris-model-parameters.json` has retained content-store bytes. Native
command success determines acceptance. Actual held-out accuracy below the
selected minimum exits nonzero. Direct local training proves the command's
result; completing this Plan additionally requires the original Fleet execution
and retained-artifact evidence. A local verification wrapper cannot publish
the model to the strict content store.
