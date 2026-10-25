# Official Airflow TaskFlow → OH-Goon showcase

A complete Python repository for the official [Airflow TaskFlow tutorial](https://airflow.apache.org/docs/apache-airflow/3.3.2/_modules/airflow/example_dags/tutorial_taskflow_api.html), adapted into independently testable `extract → transform → load` operations.

The original sample values 301.27 + 433.21 + 502.22 give **1236.70**. `changed` gives **60.00**; `invalid` causes transformation failure. The latter two fixtures are showcase extensions.

## Local development

From this directory, which can be published as its own Git repository:

```bash
uv sync
uv run pytest
uv run orders-pipeline extract --fixture official
uv run orders-pipeline transform --data '{"1001":301.27,"1002":433.21,"1003":502.22}'
uv run orders-pipeline load --total 1236.70
```

The Python source and tests do not depend on Airflow or OH-Goon. Each CLI command writes its declared result to `.showcase/`. The executable project has one `pyproject.toml` and lock; managed engine environments should not modify the developer's virtualenv.

## Airflow

Use an independently installed **Airflow 3** environment following its official setup documentation; install this package there and register `dags/orders_taskflow.py`. Trigger `orders_taskflow_comparison`. Airflow TaskFlow passes small Python results between tasks using XCom. Choose a fixture using `ORDERS_FIXTURE` in the Airflow task environment and inspect the three native task instances and logs.

## OH-Goon

Register this directory as a standalone target Git repository in OH-Goon. Its root `goon.yml` uses current supported `uv.group` authoring and three Step output handoffs. Select it via `source: {kind: repository-goon}` in a `run.yml` with the registered repository ID, then use `oh-goon config inspect`, `config validate`, `lock` and `run` with that path. Inspect the exact Run, Steps, logs, outputs, and final artifact in Game Tape. This does **not** require Airflow to be installed.

The proposed `runtime: python` complete-package contract in PR #51 is not claimed implemented here; this repository-root example avoids unsupported new syntax.

## What the showcase does and does not prove

Both engines invoke **the same** `orders_pipeline.operations`. Airflow owns its DAG, task state and logs; OH-Goon owns the linear Step sequence and Game Tape. Neither engine runs the other. Real engine-run proof requires actual admission, execution and readback; local Python tests alone do not certify either engine. This does not convert arbitrary branching/parallel DAGs to a linear OH-Goon sequence.
