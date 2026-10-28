# Run the orders TaskFlow example from Git

Classification: runnable-with-prerequisites

Select one revision of this repository and run its complete
[general/orders-taskflow Python project](../../templates/goons/general/orders-taskflow/README.md)
against your actual Orders application Git checkout. This trusted-local
verification Plan needs an initialized Oh-Goon CLI, Python 3.12+ and native uv.
The application has no runtime dependencies; the separate Airflow adapter is
optional.

## Develop and select the application

In your Orders application checkout, first develop and run the same business
code directly:

```bash
uv sync --frozen --no-default-groups --group dev
uv run --no-sync pytest tests/test_operations.py tests/test_cli.py
uv run --no-sync orders-pipeline run --fixture official
```

The result in `.showcase/result.json` contains `{"total_order_value": "1236.70"}`.
The installed command's `run` form calls the same extraction, transformation and
loading functions that the three Oh-Goon Steps invoke individually.

Register this real application repository and initialize by its path:

```bash
oh-goon league add-folder "$PWD"
oh-goon templates init general/orders-taskflow --kind goon --output ../orders-workflow --repository "$PWD"
```

Keep the generated `target` and `inputs` in `../orders-workflow/run.yml`;
replace only its `source` mapping with this explicit Git selection:

```yaml
source:
  kind: git-plan
  git: https://github.com/oh-goon/oh-goon.git
  ref: main
  path: .
  plan: examples/airflow-taskflow-showcase/plan.yml
```

The selector names the repository, requested ref, authoring root and contained
Plan. That Plan imports the package from the same revision. Its captured Git
source supplies the executed implementation; your application checkout remains
the registered execution subject. The initializer's local `plan.yml` is now
unselected. This directory's `run.yml` is a reference selection; initialization
has already supplied your actual target identity.

```bash
oh-goon lock ../orders-workflow/run.yml
oh-goon run ../orders-workflow/run.yml
```

`config inspect ../orders-workflow/run.yml --acquire` is optional: it explicitly
acquires source and shows its input contract and selected commit without
installing or executing application code. Locking records the exact commit in
`../orders-workflow/goon.lock`; later Runs consume that pin. Editing `fixture`
and locking again preserves the pin. Only `lock --update-goons` explicitly
refreshes Git source selection. `uv.lock` owns native Python dependency
resolution; `goon.lock` additionally binds selected workflow source and caller
inputs. Cached inspection without `--acquire` does not fetch missing source.

## Inspect and export the result

The Run waits for completion and prints its Run ID and export command. Use that
ID in a new CLI process, and export into a directory outside the application
source checkout:

```bash
mkdir -p ../orders-results
oh-goon plan tape RUN_ID --json
oh-goon plan outputs RUN_ID --json
oh-goon plan export-artifact RUN_ID --step general-orders-taskflow.load --path .showcase/result.json --output ../orders-results/official.json
```

The final JSON uses `retention: content_store`. Export verifies the recorded
artifact's raw byte hash and size and the retained object's separate canonical
identity. It reads the recorded retained reference after attempt scratch is
disposed; the developer's `.venv` and direct-command output stay unchanged.
Choose a new destination filename for each export. Existing files are refused.
See the [retention lifecycle](../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md)
for retained-reference availability.

Set `inputs.fixture: changed`, lock and run again, then export that Run to
`../orders-results/changed.json`; its total is `60.00`. Both Run IDs remain
independently inspectable. Export the first ID to a fresh filename after the
later Run to recover its original `1236.70` bytes.

With `fixture: invalid`, extraction passes, transformation fails with a native
nonzero exit, and loading is skipped. `run` exits 1 and the Run requires
attention. `plan steps FAILED_RUN_ID --json` exposes those three outcomes.
Exporting `.showcase/result.json` for that failed Run is refused and creates no
destination file, even if the developer checkout or a later Run has a successful
result. DATA and TOTAL remain explicit Step output bindings; final retained
content does not replace dataflow or turn a failed Step into success.

Oh-Goon executes three ordered Steps. It does not execute or translate Airflow
DAGs. The [canonical project guide](../../templates/goons/general/orders-taskflow/README.md)
identifies the upstream tutorial and this application's adaptations. Ordinary
application tests run under the native lock without Airflow. The optional
external Airflow adapter and scheduler/worker deployment remain separate
qualification, as do strict isolation and APP certification. This local Plan
does not claim enforced CPU or memory caps.

The [Python CI walkthrough](../python-ci/README.md) uses this same Orders
application for linting, tests, packaging and artifact collection through
GitHub Actions or GitLab CI. The hosted platform owns triggering; the existing
Oh-Goon Run and native tools own execution.
