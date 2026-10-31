"""Native execution proof for the separately installed Airflow environment."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = [
    pytest.mark.airflow,
    pytest.mark.skipif(
        importlib.util.find_spec("airflow") is None
        and os.environ.get("OH_GOON_REQUIRE_AIRFLOW_TESTS") != "1",
        reason="Airflow adapter requires its separate environment",
    ),
]


def test_copied_dag_native_runs_and_failure_propagation(tmp_path):
    assert importlib.util.find_spec("airflow") is not None, (
        "Select the airflow dependency group to run this explicitly requested native adapter test"
    )
    home = tmp_path / "airflow"
    dag_folder = home / "dags"
    dag_folder.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "dags" / "orders_taskflow.py"
    shutil.copy2(source, dag_folder)
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("AIRFLOW")
    }
    env.update(
        AIRFLOW_HOME=str(home),
        AIRFLOW__CORE__DAGS_FOLDER=str(dag_folder),
        AIRFLOW__CORE__LOAD_EXAMPLES="False",
        AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=f"sqlite:///{home / 'airflow.db'}",
        AIRFLOW__LOGGING__BASE_LOG_FOLDER=str(home / "logs"),
    )
    proof = dedent("""\
        import os

        import pendulum
        from airflow.dag_processing.dagbag import DagBag
        from airflow.models.xcom import XComModel
        from airflow.utils.session import create_session
        from sqlalchemy import select

        bag = DagBag(dag_folder=os.environ["AIRFLOW__CORE__DAGS_FOLDER"])
        assert not bag.import_errors, bag.import_errors
        dag = bag.dags["orders_taskflow_comparison"]
        assert {task.task_id: task.downstream_task_ids for task in dag.tasks} == {
            "extract_orders": {"transform_orders"},
            "transform_orders": {"load_orders"},
            "load_orders": set(),
        }
        for day, fixture, expected in [
            (1, "official", "1236.70"),
            (2, "changed", "60.00"),
            (3, "invalid", None),
        ]:
            os.environ["ORDERS_FIXTURE"] = fixture
            run = dag.test(logical_date=pendulum.datetime(2026, 10, day, tz="UTC"))
            states = {ti.task_id: ti.state for ti in run.get_task_instances()}
            with create_session() as session:
                result = session.scalar(
                    select(XComModel.value).where(
                        XComModel.run_id == run.run_id,
                        XComModel.task_id == "load_orders",
                        XComModel.key == "return_value",
                    )
                )
            if expected is not None:
                assert run.state == "success", (fixture, run.state)
                assert states == {
                    "extract_orders": "success",
                    "transform_orders": "success",
                    "load_orders": "success",
                }, states
                assert result == {"total_order_value": expected}, result
            else:
                assert run.state == "failed", run.state
                assert states == {
                    "extract_orders": "success",
                    "transform_orders": "failed",
                    "load_orders": "upstream_failed",
                }, states
                assert result is None, result
        """)
    log_path = tmp_path / "native-airflow.log"
    commands = [
        [sys.executable, "-m", "airflow", "db", "migrate"],
        [sys.executable, "-c", proof],
    ]
    with log_path.open("wb") as log:
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=tmp_path,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
            if completed.returncode:
                with log_path.open("rb") as diagnostic:
                    diagnostic.seek(max(0, log_path.stat().st_size - 12000))
                    pytest.fail(diagnostic.read().decode(errors="replace"))
