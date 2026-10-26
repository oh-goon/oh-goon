# ml/iris-train

Train and evaluate scikit-learn's logistic-regression pipeline through this
package's installed `iris-train` command.

**Status:** teaching example. **Effects:** Runs training/evaluation and writes
metrics and model parameters. **Prerequisites:** Python 3.12+, native uv, and
the selected Plan's admitted strict executor and content-store publication.

## Develop the complete project

This directory is the canonical Python project; it installs after relocation
without the Oh-Goon backend or a separate certification fixture. From here:

```sh
uv sync --locked
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync iris-train --random-seed 42 --test-size-percent 20 --minimum-accuracy-percent 90 --max-iterations 300
```

`pyproject.toml` owns the console entry point, scikit-learn runtime dependency,
development tools and build backend; committed `uv.lock` owns native dependency
resolution. Local development includes `dev`; package execution installs only
the project and runtime dependencies by default. The Goon owns workflow input
defaults and constraints; the CLI requires explicit arguments.

The required integration CI check and ordinary `make release-check` run this
project's own pytest suite through `make test-python-projects`, using its native
lock and a disposable environment outside the checkout.

The command fits scaling and logistic regression on the training partition and
evaluates held-out rows from scikit-learn's bundled Iris dataset. The default
split uses 120 training and 30 evaluation examples. It writes both declared
JSON files and exits nonzero when actual accuracy misses the selected minimum.
Model parameters contain fitted scaler/classifier arrays, without a pickle payload.

## Run through Oh-Goon

```sh
oh-goon templates init ml/iris-training --kind plan --output ./workflow
```

Set the registered `target.repository_id` and any caller choices in
`workflow/run.yml`, then [inspect, validate, lock and run](../../../README.md#first-local-python-run).
[`goon.yml`](goon.yml) owns the typed input contract.
The [ml/iris-training Plan](../../../plans/ml/iris-training/README.md) supplies
the execution policy and ordered work. Its target need not implement `iris-train`.

`runtime: python` captures and installs this complete package. Execution uses
owned source, environment and writable output roots rather than the target
project or developer `.venv`. Local caches and declared `ml-output/` files
do not become package source identity. Authored code, metadata, resources and
native lock changes require relocking.

**Artifact retention:** `ml-output/iris-training-metrics.json` is `evidence`;
`ml-output/iris-model-parameters.json` uses `content_store` because its bytes
are the reusable training product. See the [retention contract](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md).
The Plan retains `purpose: cicd`. A verification-purpose wrapper refuses this
content-store declaration before training. Native local command success alone
does not establish strict Fleet execution or retained-artifact publication.
The default policy trusts package code. With explicit strict
`trust_repository_code: false`, native installation and builds use the selected
Step isolation boundary, a verified private build copy and an attempt-owned
environment. Untrusted attempts start with an empty uv cache, and the current
remote provider requires a deny-all VM. This package's registry dependencies
therefore need a self-contained source variant for that profile; otherwise native
installation fails before training. The shipped package keeps its trusted-code
policy and does not fall back from strict builds to the host. Successful
installation alone does not establish strict qualification.
