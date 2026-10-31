# general/orders-taskflow

**Status:** teaching example. **Effects:** Extracts, transforms and loads fixture orders through three installed commands. **Prerequisites:** Python 3.12+ and native uv. The Airflow adapter selects the separate optional `airflow` dependency group.

One standalone Python application adapts the official [Airflow 3.3.2 TaskFlow tutorial source](https://airflow.apache.org/docs/apache-airflow/3.3.2/_modules/airflow/example_dags/tutorial_taskflow_api.html) into shared `extract → transform → load` operations. That version's `tutorial_taskflow_api` reads a hardcoded JSON string, sums the values with ordinary Python arithmetic, returns `total_order_value`, and prints it to two decimal places. Its amounts 301.27 + 433.21 + 502.22 give **1236.70**.

This project's packaged input resources, Decimal arithmetic, validation, installed CLI and output files are showcase adaptations. Its additional `changed` fixture gives **60.00**; `invalid` fails the shared transformation. The adapted application is not a copy of the upstream program. OH-Goon runs three ordered Steps; it does not execute, parse or convert an Airflow DAG. Airflow remains an optional external comparison.

## Local development

Enter this package directory directly, or copy the whole directory outside the OH-Goon checkout. It is a complete Python project with a committed native `uv.lock`; Python 3.12+ and uv are required:

```bash
uv sync --frozen --no-default-groups --group dev
uv run --no-sync pytest tests
uv run --no-sync orders-pipeline run --fixture official
uv run --no-sync orders-pipeline extract --fixture official
uv run --no-sync orders-pipeline transform --data '{"1001":301.27,"1002":433.21,"1003":502.22}'
uv run --no-sync orders-pipeline load --total 1236.70
```

The application and its ordinary tests need neither Airflow nor OH-Goon. `run --fixture` calls the same extraction, transformation and loading functions directly, stopping on a real application exception. The individual commands expose each business operation. Use `changed` to observe a different total or `invalid` to observe transformation failure. Each writes its result under `.showcase/` in the working directory; use a fresh working directory or remove old demonstration outputs before inspecting a failure.

`extract` and `run` require an explicit fixture; the Goon owns the workflow default. `transform --data` accepts JSON data, and `load --total` accepts the scalar total. OH-Goon transports the preceding Step's actual output value into those arguments. Its Goon deliberately invokes the three individual commands as three separately observed Steps.

Native frozen synchronization consumes the committed resolution without rewriting it or checking metadata freshness. The development environment explicitly selects `dev` for pytest; OH-Goon's managed workload installs base dependencies only in its separate owned environment.

The native project pytest configuration discovers the whole test directory and
excludes the optional `airflow` marker from ordinary development and portable
daily checks. New ordinary tests join discovery automatically. The comparison
below explicitly overrides that marker after selecting its separate dependency
group; deselection is not reported as an executed or skipped adapter test.

## OH-Goon

Use your actual Orders application Git checkout, with this complete project at
its repository root. The [Git-source walkthrough](../../../../examples/airflow-taskflow-showcase/README.md)
uses the same real application as its registered subject. Source-checkout CLI
setup is described in the [Quickstart](../../../../docs/QUICKSTART.md).

Initialize by the project path; no manual repository ID transfer is needed:

```bash
oh-goon league add-folder "$PWD"
oh-goon templates init general/orders-taskflow --kind goon --output ../orders-workflow --repository "$PWD"
```

To execute this checkout's own root Goon, keep the generated target identity and
replace the generated `source` in `../orders-workflow/run.yml` with:

```yaml
source: {kind: repository-goon}
inputs:
  fixture: official
```

The included `run.yml` illustrates this selection, while the initializer fills
the registered target. Run the normal lock/waited-execution path:

```bash
oh-goon lock ../orders-workflow/run.yml
oh-goon run ../orders-workflow/run.yml
```

The expected result is three successful Steps and `total_order_value` of
`1236.70`. `config inspect` and `config validate` are optional diagnostics.
Editing an input or source requires explicitly locking again; running never
refreshes a stale lock. The [Git-selected example](../../../../examples/airflow-taskflow-showcase/README.md)
selects the Plan and package from one pinned upstream revision instead, while
retaining the same real application repository as subject.

Run prints its ID and an export command with the exact recorded Step ID.
`plan tape`, `plan steps`, `plan logs` and `plan outputs` inspect that Run;
raw process logs have their own transient availability. The final
`.showcase/result.json` uses `retention: content_store`, so its actual bytes can
be exported after attempt cleanup or process restart:

```bash
mkdir -p ../orders-results
oh-goon plan outputs RUN_ID --json
oh-goon plan export-artifact RUN_ID --step STEP_ID --path .showcase/result.json --output ../orders-results/official.json
```

Use the Step ID printed by completion or `plan outputs`. Export checks the
recorded retained reference, raw file hash and size before creating a new local
file. Existing destinations are refused. Retention is owned by the
[existing content store](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md);
it does not grant strict executor or certification guarantees.

Set `inputs.fixture` to `changed`, relock and run. Export its `60.00` result to a
new filename, then reopen and export the earlier Run's original `1236.70` result.
With `fixture: invalid`, extraction passes, transformation fails, loading is
skipped and `run` exits 1. That Run has no load artifact; an export attempt is
refused even if a later successful Run or direct developer command has a result
file. The three managed Steps transport actual DATA and TOTAL values through
the existing explicit output bindings.

This root Goon selects `runtime: python`. It captures this complete project and
installs its base application dependencies with no default dependency groups.
Native uv owns installation; there is no dependency-install Step. Execution
uses separate package source, environment and writable output roots. The
developer's `.venv` and direct `.showcase/` outputs remain unchanged. Authored
code, resources, project metadata and native lock bind freshness; caches and
declared `.showcase/` outputs do not. The ordinary verification Plan makes no
claim of enforced CPU or memory limits.

## Airflow

This project's explicit optional `airflow` group pins **Airflow 3.3.2** and its native lock records the complete dependency resolution. Select it in a separate Python 3.12 environment:

```bash
uv sync --frozen --no-default-groups --group dev --group airflow --python 3.12
OH_GOON_REQUIRE_AIRFLOW_TESTS=1 uv run --no-sync pytest -m airflow tests/test_airflow_adapter.py
```

The ordinary application environment does not install Airflow. Register `dags/orders_taskflow.py` in the selected Airflow environment's DAG folder; the adapter reads installed package resources and can be copied independently of the source tree. Follow the [official installation instructions](https://airflow.apache.org/docs/apache-airflow/3.3.2/installation/installing-from-pypi.html) for a separate scheduler/worker deployment.

Trigger `orders_taskflow_comparison`. Set `ORDERS_FIXTURE=official`, `changed`, or `invalid` in the task environment. Airflow owns that environment selection; it does not read OH-Goon's `run.yml`. TaskFlow passes the small Python results through native XCom. Inspect the three task instances, logs and final returned `total_order_value`. The invalid fixture must fail `transform_orders` and prevent `load_orders` from succeeding.

The explicitly selected adapter test uses isolated temporary SQLite state to verify a copied DAG's native import, dependency graph, complete local `dag.test()` runs, XCom totals and invalid-input failure propagation. It is optional and does not run as an OH-Goon acceptance gate. A scheduler/worker deployment remains a separate verification boundary.

## Scope and the Python Goon contract

Both adapters use the same installed `orders_pipeline.operations`. Airflow can run ordinary Python functions and represents dependencies as a DAG. OH-Goon uses its ordered Plan → Goon → Step model and explicit output bindings; it neither stores nor computes DAG dependencies. A dependency-respecting order is sufficient for these three operations. Equivalent results here do not establish general DAG equivalence, relative performance, or reliability.

This example uses the [complete Python Goon contract](https://github.com/oh-goon/oh-goon/blob/main/docs/architecture/ADR_PYTHON_GOON_PROJECTS.md):
one installable application, explicit workflow inputs, package-owned runtime,
base-only dependency selection and captured authored source. The shipped
[Iris Goon](https://github.com/oh-goon/oh-goon/tree/main/templates/goons/ml/iris-train)
uses the same package contract. Reusable packages can also be selected from
explicit pinned Git coordinates; see the [template guide](https://github.com/oh-goon/oh-goon/blob/main/templates/README.md#select-pinned-git-source).
This root-Goon comparison does not establish scheduler/worker deployment,
strict/native qualification, APP certification or the broader initialization/result roadmap.

The required integration CI check and ordinary `make release-check` run this package's application tests and the Iris package tests in disposable environments through `make test-python-projects`, without selecting Airflow. The canonical strict integration ring keeps its prepared-environment contract. The [Git-selected example](../../../../examples/airflow-taskflow-showcase/README.md) runs this same named package against the real developer application repository.

The [Python CI walkthrough](../../../../examples/python-ci/README.md) reuses this
application for linting, tests, packaging and retained result collection. Its
GitHub Actions and GitLab examples leave triggering with the hosted platform;
the same original Plan/Goon/Step lifecycle executes the declared work.

The existing native CLI regression in `backend/tests/integration/services/test_airflow_taskflow_showcase.py` executes `official` success → `invalid` failure → `changed` success through both local and Git Plan selections. Each case first develops, tests and runs the real project through native uv. Managed invocations obtain distinct Run IDs and preserve that developer environment and direct output. After attempt scratch disposal, fresh CLI processes reopen all three IDs, compare retained source/selection and Step evidence, and export the successful Runs' exact JSON bytes. The failed Run has no load artifact and cannot export one after the later success. These checks establish this showcase's recorded results and retained bytes; they do not prove a separate crash-recovery system or strict execution.
