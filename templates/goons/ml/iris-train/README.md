# ml/iris-train

Train and evaluate scikit-learn's logistic-regression pipeline through this
package's installed `iris-train` command.

**Status:** teaching example. **Effects:** Runs training/evaluation and retains
measured metrics and model parameters. **Prerequisites:** Python 3.12+, native
uv and the selected Plan's execution requirements. The shipped
[ml/iris-training Plan](../../../plans/ml/iris-training/README.md) runs trusted
local training without strict executor setup.

## Develop the complete project

This directory is the canonical Python project; it installs after relocation
without the Oh-Goon backend or a separate certification fixture. From here:

```sh
uv sync --frozen --no-default-groups --group dev
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync iris-train --random-seed 42 --test-size-percent 20 --minimum-accuracy-percent 90 --max-iterations 300
```

`pyproject.toml` owns the console entry points and native ML runtime dependencies,
development tools and build backend; committed `uv.lock` owns native dependency
resolution. Frozen synchronization consumes that lock without a metadata-freshness
check or rewrite. Local development explicitly selects `dev`; package execution
installs only the project and runtime dependencies by default. The Goon owns
workflow input defaults and constraints and passes their resolved values to the
application. The standalone CLI requires the training choices shown above;
dataset selection and training rounds are optional there.

The required integration CI check and ordinary `make release-check` run this
project's own pytest suite through `make test-python-projects`, using its native
lock and a disposable environment outside the checkout.

The command fits scaling and logistic regression on the training partition and
evaluates held-out rows from scikit-learn's bundled Iris dataset. The default
split uses 120 training and 30 evaluation examples. It writes:

- `ml-output/iris-training-metrics.json`: actual accuracy and threshold, seed,
  test-size percentage, maximum iterations, row counts and library version.
- `ml-output/iris-model-parameters.json`: fitted scaler/classifier arrays,
  classes and library version, without a pickle payload.

The command exits nonzero when actual accuracy misses the selected minimum;
its measured metrics and parameters are still written. Invalid arguments are
refused before training or output creation. Repeating the same seed reproduces
the parameters in the same native environment; a seed change can produce
different parameters even when accuracy is unchanged.

## Exercise sustained training and progress

Select the typed `training_rounds` input for a finite workload of repeated native
fits. Its default is one fit, preserving the introductory command's output and
artifacts. A standalone invocation can select `--training-rounds 1000` alongside
the explicit training choices shown above.

Each round creates and fits the same scikit-learn pipeline on the same seeded
split; only the final fit is serialized. This exercises genuine numerical work
and process supervision, not incremental learning, checkpoint recovery or a retry
policy. Repeating identical choices reproduces the final model in the same native
environment. Multi-round metrics additionally record `training_rounds` and
`completed_training_rounds`.

Multi-round work flushes a starting line before fitting and bounded progress lines
after completed fits. These contain only completed/total counts, with at most 102
lines across the supported range; they do not contain dataset rows or identifiers.
They use the existing `OH-GOON ACTIVITY:` prefix so a waiting Oh-Goon CLI can
display progress without reading private raw output.
The selected Run/Step owners retain resource limits, deadlines, cancellation,
terminal results and artifacts. Training output is progress, not completion proof.
The final model and metrics are written only after every fit completes; an
interruption during fitting does not publish that pair. A later output failure
or interruption still remains a failed or cancelled Run even when files exist.

## Select a captured dataset

`iris-train` keeps native scikit-learn Iris data as its default `--dataset builtin`.
The Goon exposes the finite `dataset` choice: `builtin` or `captured`. Selecting
`captured` reads only this project's `src/iris_train/data/training.json` resource;
it accepts no dataset pathname or URL and never falls back to builtin data when
the resource is missing or invalid.

The UTF-8 JSON object contains exactly `data`, `target` and optionally `record_ids`.
`data` has at least 30 rows of four finite numbers; `target` has the same number
of integer labels, each 0, 1 or 2. Optional `record_ids` has one string per row,
at most 256 UTF-8 bytes each. Identifiers are validated and then ignored by
training and reporting. The entire resource is limited to 64 KiB. Duplicate
keys, invalid encoding, nonfinite numbers, runtime symlinks and special files refuse
with `IRIS_DATASET_INVALID` before model outputs are created. Safe captured
resource access requires native no-follow directory/file opens and nonblocking
regular-file observation; unavailable platform support refuses rather than
using a weaker reader.

Native scikit-learn still owns splitting, fitting and scoring. Successful
captured-data metrics add `dataset_sha256`, the SHA-256 of the exact read bytes,
alongside the actual training/test counts. Builtin metrics remain unchanged.
Raw rows and record identifiers are not printed or put in scalar outputs.
Fitted parameters and aggregate metrics remain intentional artifacts, not a
claim of general statistical privacy or sandbox isolation.

Place the resource inside the selected complete project/application before
capture and explicitly prepare after changing its bytes. Included resources
change the original project and execution-lock identities. Select
`inputs: {dataset: captured}` in a workflow using this Goon; use the original
Run/artifact owners to compare distinct Runs and retain earlier results. The
separate `iris-pipeline` command keeps its public-data scalar contract and does
not consume this private resource.

## Develop the staged pipeline

The same project also installs `iris-pipeline`, with separate `ingest`, `train`,
`evaluate` and `publish` commands. The [staged workflow example](../../../../examples/iris-pipeline/README.md)
uses those commands through four ordinary Steps and the existing retained-artifact
owner. This package's original `iris-train` command and single-Step Goon keep the
training behavior described above.

After native synchronization, use a fresh output directory and the installed
commands. From this project directory in a POSIX shell:

```sh
iris_project="$PWD"
task_pipeline_output="$(mktemp -d)"
cd "$task_pipeline_output"
"$iris_project/.venv/bin/iris-pipeline" ingest --random-seed 42 --test-size-percent 20
"$iris_project/.venv/bin/iris-pipeline" train --data "$(cat ml-pipeline/data.json)" --max-iterations 300
"$iris_project/.venv/bin/iris-pipeline" evaluate --data "$(cat ml-pipeline/data.json)" --model "$(cat ml-pipeline/model.json)" --minimum-accuracy-percent 90
"$iris_project/.venv/bin/iris-pipeline" publish --model "$(cat ml-pipeline/model.json)" --verdict "$(cat ml-pipeline/evaluation.json)"
```

Ingestion loads the bundled Iris data and splits it once. Training consumes the
actual split, evaluation restores the fitted scaler/classifier state and calls
native scikit-learn prediction/scoring, and publication requires a successful
verdict matching that model and data. Each JSON handoff is bounded to 16 KiB.
These small public teaching values can enter command arguments and Run evidence;
this is not a private or large-dataset intake mechanism.

Only publication creates `ml-output/iris-model.joblib`, along with the metrics
and parameter JSON files. The binary never travels in a scalar output. Existing
destinations are refused, and a below-threshold evaluation exits nonzero. Use a
fresh output directory for another native pipeline; the Oh-Goon example gives
each Step its own attempt workspace. Load joblib models only from a trusted
producer, such as this completed workflow's verified retained export.

## Run through Oh-Goon

Follow the [ml/iris-training walkthrough](../../../plans/ml/iris-training/README.md#train-locally)
for exact-target initialization, optional input inspection, the original Run and
artifact export. That Plan's unique import exposes this Goon's defaults and
constraints; it does not copy or redefine the input schema. An unchanged
initialized workflow already has its lock. Prepare again after changing source
or caller inputs.

[`goon.yml`](goon.yml) owns the typed inputs. The selected target need not
implement `iris-train`; `runtime: python` captures and installs this complete
package. Execution uses owned source, environment and writable output roots
rather than the target project or developer `.venv`. Local caches and declared
`ml-output/` files do not become package source identity. Authored code, metadata,
resources, native lock and caller-input changes require relocking.

Both JSON artifacts use `retention: content_store`: historical comparison needs
the measured metrics as well as reusable model parameters after attempt cleanup.
Collection and repository-scoped retained references use the existing owners;
there is no separate experiment store. Follow the
[Plan walkthrough](../../../plans/ml/iris-training/README.md#retrieve-and-compare-results)
to export the exact recorded files, change the seed, rerun and compare both Runs.
See the [retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md)
for content-reference lifetime.

The default Plan selects trusted local verification. Explicit strict CI/CD
Plans retain their admitted executor and isolation requirements; local model
publication is not evidence of those guarantees. With explicit strict
`trust_repository_code: false`, native installation and builds use the selected
Step isolation boundary, a verified private build copy and an attempt-owned
environment. The current remote profile requires a deny-all VM with an empty uv
cache, so registry dependencies need a self-contained source variant for that
profile. Missing prerequisites fail without a host-build fallback.
