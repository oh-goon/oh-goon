"""Small native training Plan through ordinary admission and the physical Run worker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
import yaml

from oh_goon._application_foundation import build_runtime_foundation
from oh_goon._application_services import build_product_services
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunSource
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native protected Run transport")
@pytest.mark.parametrize("source", (FleetRunSource.MANUAL, FleetRunSource.CICD))
def test_native_training_plan_has_worker_custody_and_canonical_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: FleetRunSource
) -> None:
    for name, value in (
        ("OH_GOON_DB", tmp_path / "state" / "runtime.db"),
        ("XDG_CONFIG_HOME", tmp_path / "config"),
        ("XDG_DATA_HOME", tmp_path / "data"),
        ("XDG_STATE_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("OH_GOON_EXECUTORS", raising=False)
    repository = tmp_path / "repos" / "training"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    (repository / "train.py").write_text(
        "import json, os, time\n"
        "from pathlib import Path\n"
        "Path('ready').write_text(str(os.getpid()))\n"
        "deadline = time.monotonic() + 45\n"
        "while not Path('release').exists():\n"
        "    if time.monotonic() > deadline: raise TimeoutError('test release unavailable')\n"
        "    time.sleep(0.01)\n"
        "weight = 0.0\n"
        "for epoch in range(100):\n"
        "    weight -= 0.01 * sum(2*x*(weight*x-y) for x,y in ((1.,2.),(2.,4.),(3.,6.)))/3\n"
        "Path('metrics.json').write_text(json.dumps({'weight': weight}))\n"
        "print('training completed', flush=True)\n"
    )
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    plan_path = workflow / "goon.yml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "execution": {"purpose": "verification"},
                "steps": [{"id": "train", "run": f"{sys.executable} train.py"}],
            }
        )
    )
    foundation = build_runtime_foundation(standard_local=True, startup_purpose="workflow")
    runtime = build_product_services(foundation)
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(plan_path)
        target = runtime.plans.prepare_execution_target(plan_path, repository=repository)
        repository_id = RepositoryIdentity.model_validate({"path": repository}).repository_id
        run = runtime.runs.start_prepared_plan(
            target,
            repository_ids={repository_id},
            fleet_run_id=f"training-{source.value.lower()}",
            source=source,
        )
        deadline = monotonic() + 15
        while not (repository / "ready").exists():
            if monotonic() >= deadline:
                pytest.fail("native training Step did not start")
            Event().wait(0.01)
        with foundation.database.transaction() as connection:
            lease = connection.execute(
                "SELECT custody_phase, owner_pid, owner_session_id FROM execution_lease"
                " WHERE fleet_run_id=?",
                (run.fleet_run_id,),
            ).fetchone()
        assert lease is not None and lease[0] == "WORKER_OWNED"
        assert lease[1] != os.getpid()
        assert observe_process_birth(lease[1]) is not None
        assert lease[2] != foundation.database.session_id
        current = runtime.runs.get(run.fleet_run_id)
        assert current.run.status.value == "RUNNING"
        scope = ("--repository-id", repository_id, "--plan-id", target.plan.plan_id)
        with foundation.database.transaction() as connection:
            before_observers = tuple(connection.iterdump())
        for arguments in (
            ("doctor", "--json"),
            ("doctor", "--runtime", "--json"),
            ("runs", "list", "--limit", "1", *scope, "--json"),
            ("runs", "show", run.fleet_run_id, "--json"),
            ("runs", "show", "--last", *scope, "--json"),
            ("runs", "explain", run.fleet_run_id, "--json"),
        ):
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                observed = subprocess.run(
                    [str(Path(sys.executable).with_name("oh-goon")), *arguments],
                    cwd=tmp_path,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=errors,
                    timeout=15,
                    check=False,
                )
                output.seek(0)
                errors.seek(0)
                stdout, stderr = output.read(65_537), errors.read(65_537)
            assert observed.returncode in ((0, 2) if arguments[0] == "doctor" else (0,)), stderr
            assert not stderr
            assert len(stdout) <= 65_536
            report = json.loads(stdout)
            if arguments[0] == "doctor":
                assert report["scope"] == ("runtime" if "--runtime" in arguments else "tools")
                assert report["paths"]["database"] == str(foundation.database.path)
                if "--runtime" in arguments:
                    assert "runtime_safety" in report
            else:
                if arguments[1] == "list":
                    assert len(report["items"]) == 1
                    assert report["next_before_revision"] is None
                    report = report["items"][0]
                assert report["fleet_run_id"] == current.run.fleet_run_id
                assert report["status"] == current.run.status.value
                assert report["outcome"] == current.outcome.value
            with foundation.database.transaction() as connection:
                assert tuple(connection.iterdump()) == before_observers
            assert observe_process_birth(lease[1]) is not None
            assert not (repository / "metrics.json").exists()
        (repository / "release").touch()
        detail = runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=20)
        assert detail.outcome is FleetRunOutcome.SUCCESS
        assert detail.run.source is source
        assert json.loads((repository / "metrics.json").read_text())["weight"] == pytest.approx(
            2.0, abs=0.001
        )
        output = foundation.live_output.snapshot(run.fleet_run_id)
        assert "training completed" in "".join(chunk.text for chunk in output.chunks)
        with foundation.database.transaction() as connection:
            assert connection.execute("SELECT 1 FROM execution_lease").fetchone() is None
    finally:
        (repository / "release").touch()
        runtime.shutdown(wait_for_runs=True)
