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

`pyproject.toml` owns the console entry point, scikit-learn runtime dependency,
development tools and build backend; committed `uv.lock` owns native dependency
resolution. Frozen synchronization consumes that lock without a metadata-freshness
check or rewrite. Local development explicitly selects `dev`; package execution
installs only the project and runtime dependencies by default. The Goon owns
workflow input defaults and constraints; the application CLI requires explicit
arguments.

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

## Run through Oh-Goon

Register your actual project repository and initialize by path:

```sh
oh-goon league add-folder /absolute/path/to/your/project
oh-goon init --template ml/iris-training --kind plan --output ./workflow --repository /absolute/path/to/your/project
# Edit workflow/run.yml inputs if needed.
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml
```

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
