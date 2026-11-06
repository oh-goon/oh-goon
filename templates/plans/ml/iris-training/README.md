# ml/iris-training

Train and evaluate using the complete Python project in
[ml/iris-train](../../../goons/ml/iris-train/README.md), then retain the measured
metrics and fitted model parameters.

**Status:** teaching example. **Effects:** Runs trusted local training and
retains two JSON files through the existing Fleet content store.
**Prerequisites:** Python 3.12+, native uv, an initialized Oh-Goon CLI and your
registered project repository. No strict executor setup is needed for this Plan.
The target repository needs no Iris implementation or `ml` dependency group.

## Train locally

Develop and test the [Iris Python project](../../../goons/ml/iris-train/README.md#develop-the-complete-project)
with native uv first. Use your actual development Git repository as the target
and choose a new workflow directory outside that Python package:

```sh
task_workflow=/absolute/path/to/iris-workflow
oh-goon init --template ml/iris-training --output "$task_workflow" --repository /absolute/path/to/your/project --no-input
oh-goon run "$task_workflow/run.yml" --no-input
```

The output directory must not exist and its parent must already exist.
Initialization registers only the exact Git root when needed, fills the target
and resolved input defaults, and writes the first lock. An unchanged initialized
workflow is ready to run; no second preparation is needed. The Plan's unique
import exposes the [Goon's typed inputs](../../../goons/ml/iris-train/goon.yml)
without copying their schema or defaults into the Plan.

Run waits for completion and prints the Run ID, outcome and artifact evidence.
Optional `oh-goon workflow inspect "$task_workflow/run.yml"` shows those input
contracts; `workflow validate` checks the selection without executing training.
Edit only caller choices in the selected `run.yml`, then prepare before the next
Run as shown below. The [source CLI setup](../../../../README.md#cli-first--no-frontend-setup) prepares
the CLI without building the frontend.

The Plan stays small: `purpose: verification` and one ordered Goon import.
Native uv installs the captured package's base runtime dependencies in an owned
environment; the developer's environment and target source stay unchanged.
The command trains a scaler/logistic-regression pipeline on scikit-learn's
bundled Iris data. Its exit code determines success. The default seed and
threshold accept the measured held-out result; a 100% threshold with seed 42
returns exit 1. A failed training Step stops later ordinary Steps in a Plan.

## Retrieve and compare results

Use the printed export commands or substitute the completed Run ID below.
`runs outputs` reports the declared path, hash, size and retained reference.
Export selects that Run's recorded artifact and verifies its retained bytes:

```sh
task_results=/absolute/path/to/iris-results
mkdir -p "$task_results"
oh-goon runs outputs RUN_ID --json
oh-goon runs export-artifact RUN_ID --step ml-iris-train.iris-train --path ml-output/iris-training-metrics.json --output "$task_results/metrics-seed42.json"
oh-goon runs export-artifact RUN_ID --step ml-iris-train.iris-train --path ml-output/iris-model-parameters.json --output "$task_results/model-seed42.json"
oh-goon runs tape RUN_ID --json
```

Metrics contain actual accuracy, the minimum accuracy, seed, test-size
percentage, maximum iterations, row counts and scikit-learn version. The model
file contains fitted scaler/classifier arrays, classes and library version;
it is JSON, not a pickle or a standalone inference program. Game Tape binds
the command and captured project identity to the Run and artifact evidence.

Change `inputs.random_seed` to `7` in `$task_workflow/run.yml`, then prepare and
run the same configuration:

```sh
oh-goon workflow prepare "$task_workflow/run.yml"
oh-goon run "$task_workflow/run.yml" --no-input
```

Changing an input requires relocking; attempting to run the old lock is refused
before training. Export the second Run's files to `$task_results/metrics-seed7.json`
and `$task_results/model-seed7.json`, then compare them with the first files using
your normal JSON tools or `diff -u`. Equal accuracy does not mean equal fitted parameters. Both
Run IDs remain independently inspectable after the training processes and
attempt workspaces disappear. Retention preserves explicitly declared file
bytes; the [content-store lifecycle](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md)
still owns their availability.

## Select strict execution explicitly

Local retention does not establish strict isolation or privileged native
qualification. For those requested guarantees, a caller-owned Plan can select:

```yaml
execution:
  purpose: cicd
goons:
- ml/iris-train
```

Select the file through `run.yml`, lock it and run it with the admitted strict
executor required by the [execution contract](../../../../docs/CI_CD_CONTRACT.md).
The [Git-source example](../../../../examples/python-goon-project/README.md)
keeps this explicit strict selection. With `trust_repository_code: false`,
native installation and builds stay inside the selected isolation boundary.
The current deny-all remote VM profile has an empty uv cache, so this package's
registry dependencies require a self-contained source variant for that profile;
there is no host-build fallback.
