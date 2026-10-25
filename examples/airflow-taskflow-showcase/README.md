# Official Airflow TaskFlow → OH-Goon showcase

Classification: runnable-with-prerequisites

One standalone Python application adapts the official [Airflow 3.3.2 TaskFlow tutorial](https://airflow.apache.org/docs/apache-airflow/3.3.2/_modules/airflow/example_dags/tutorial_taskflow_api.html) into shared `extract → transform → load` operations. The original values 301.27 + 433.21 + 502.22 give **1236.70**. The `changed` fixture gives **60.00**; `invalid` fails transformation. These two fixtures, decimal arithmetic, validation, the CLI, and file outputs are showcase extensions. Airflow's original sample loads by printing the total.

## Local development

Copy this entire directory to a location outside the OH-Goon checkout. It is a complete Python project with a committed native `uv.lock`; Python 3.12+ and uv are required. From the copied directory:

```bash
uv sync --locked
uv run --no-sync pytest tests/test_operations.py tests/test_cli.py
uv run --no-sync orders-pipeline extract --fixture official
uv run --no-sync orders-pipeline transform --data '{"1001":301.27,"1002":433.21,"1003":502.22}'
uv run --no-sync orders-pipeline load --total 1236.70
```

The application and its ordinary tests need neither Airflow nor OH-Goon. Each CLI operation writes its result under `.showcase/` in the working directory. `extract` requires an explicit fixture; the Goon owns the workflow default. `transform --data` accepts JSON data, and `load --total` accepts the scalar total. OH-Goon transports the preceding Step's output value into those arguments.

## OH-Goon

Use an installed OH-Goon CLI after its normal setup. For a source checkout, follow [development setup](https://github.com/oh-goon/oh-goon/blob/main/docs/DEVELOPMENT.md) and use its prepared CLI. In the copied project, initialize a standalone target repository and register it headlessly:

```bash
git init
oh-goon league add-folder "$PWD"
oh-goon league scan --json
```

Set the included `run.yml`'s `target.repository_id` to this project's canonical `repository_id` from the scan. Registration does not require the browser. The current CLI still requires this manual ID transfer; automatic repository selection belongs to the broader initialization work. Change caller values only in `run.yml` (`fixture: official`, `changed`, or `invalid`), then:

```bash
oh-goon lock ./run.yml
oh-goon run ./run.yml
```

The Run waits for completion and prints its ID. Use that exact ID in `oh-goon plan tape RUN_ID`, `oh-goon plan steps RUN_ID`, `oh-goon plan logs RUN_ID`, and `oh-goon plan outputs RUN_ID`, or open its Game Tape in the browser. `config inspect ./run.yml` and `config validate ./run.yml` are optional authoring diagnostics. Lock/run perform the canonical validation. After changing a selection or Goon declaration, explicitly lock again; running does not refresh a stale lock.

The expected result is three successful Steps and `total_order_value` of `1236.70` or `60.00`. With `fixture: invalid`, extraction succeeds, transformation fails with a nonzero exit, loading is skipped, and the Run requires attention. Use a fresh copy or remove previous `.showcase/` results when inspecting failure locally: an old file is not evidence of a new successful Step.

The final `.showcase/result.json` uses `retention: evidence`: Game Tape records its presence, hash and size. This declaration does not retain durable file bytes. See [artifact retention](https://github.com/oh-goon/oh-goon/blob/main/docs/development/TEMPLATE_ARTIFACT_RETENTION.md).

This root Goon selects `runtime: python`. It captures this complete project and
installs its base application dependencies with no default dependency groups.
Native uv owns installation; there is no dependency-install Step. Execution
uses separate package source, environment and writable output roots. It does
not reuse or prune the developer's `.venv`, and the target repository supplies
no implementation. Authored code, resources, project metadata and native lock
bind freshness; caches and declared `.showcase/` outputs do not.

## Airflow

Install **Airflow 3.3.2** in a separate Python 3.12 environment using the [official installation instructions](https://airflow.apache.org/docs/apache-airflow/3.3.2/installation/installing-from-pypi.html) and its matching constraints. Install this project in that environment, then register `dags/orders_taskflow.py` in its DAG folder. The adapter reads installed package resources and can be copied independently of the source tree.

Trigger `orders_taskflow_comparison`. Set `ORDERS_FIXTURE=official`, `changed`, or `invalid` in the task environment. Airflow owns that environment selection; it does not read OH-Goon's `run.yml`. TaskFlow passes the small Python results through native XCom. Inspect the three task instances, logs and final returned `total_order_value`. The invalid fixture must fail `transform_orders` and prevent `load_orders` from succeeding.

With Airflow and pytest installed in that separate environment, run `python -m pytest tests/test_airflow_adapter.py`. It uses isolated temporary SQLite state to verify a copied DAG's native import, dependency graph, complete local `dag.test()` runs, XCom totals and invalid-input failure propagation. A scheduler/worker deployment remains a separate verification boundary.

## Scope and the Python Goon contract

Both adapters use the same installed `orders_pipeline.operations`. OH-Goon observes three linear Steps; Airflow observes three dependent tasks. Neither engine invokes the other. Equivalent results here do not establish general DAG equivalence, relative performance, or reliability.

This example uses the [complete Python Goon contract](https://github.com/oh-goon/oh-goon/blob/main/docs/architecture/ADR_PYTHON_GOON_PROJECTS.md):
one installable application, explicit workflow inputs, package-owned runtime,
base-only dependency selection and captured authored source. The shipped
[Iris Goon](https://github.com/oh-goon/oh-goon/tree/main/templates/goons/ml/iris-train)
uses the same package contract. Reusable packages can also be selected from
explicit pinned Git coordinates; see the [template guide](https://github.com/oh-goon/oh-goon/blob/main/templates/README.md#select-pinned-git-source).
This root-Goon comparison does not establish scheduler/worker deployment,
strict/native qualification, APP certification or the broader initialization/result roadmap.
