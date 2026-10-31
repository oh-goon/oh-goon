"""An observing application cannot cancel another application's live Fleet work."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator


@pytest.mark.process
@pytest.mark.skipif(os.name != "posix", reason="Native POSIX command supervision")
@pytest.mark.parametrize("readback", ("runtime", "cli"))
def test_observer_shutdown_preserves_foreign_run_and_owner_shutdown_cancels_its_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, readback: str
) -> None:
    repository = tmp_path / "repos" / "sample"
    example = Path(__file__).resolve().parents[4] / "examples" / "standard-atomic"
    shutil.copytree(example, repository)
    entered, release = tmp_path / "entered", tmp_path / "release"
    (repository / "scripts/hello.sh").write_text(
        f"#!/bin/sh\nprintf started > '{entered}'\n"
        f"while [ ! -f '{release}' ]; do sleep 0.05; done\n"
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    database = tmp_path / "state" / "app.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    owner = ApplicationRuntime.build(standard_local=True)
    observer = None
    try:
        owner.locations.add(repository.parent)
        target = owner.plans.prepare_execution_target(repository / "goon.yml")
        admitted = owner.runs.start_prepared_plan(
            target, repository_ids={repository_id(repository)}, fleet_run_id="owned-process"
        )
        deadline = time.monotonic() + 10
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert entered.exists(), owner.runs.get(admitted.fleet_run_id)
        assert owner.runs.get(admitted.fleet_run_id).run.status is FleetRunStatus.RUNNING
        execution = ExecutionCoordinator(LocalDatabase(database))
        lease = execution.active_lease()
        assert lease is not None and lease.fleet_run_id == admitted.fleet_run_id

        if readback == "runtime":
            observer = ApplicationRuntime.build(standard_local=True)
            assert observer.runs.get(admitted.fleet_run_id).run.status is FleetRunStatus.RUNNING
            observer.shutdown(wait_for_runs=False)
        else:
            observed = subprocess.run(
                [
                    str(Path(sys.executable).with_name("oh-goon")),
                    "runs",
                    "steps",
                    admitted.fleet_run_id,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            assert json.loads(observed.stdout)["fleet_run_id"] == admitted.fleet_run_id

        assert owner.runs.get(admitted.fleet_run_id).run.status is FleetRunStatus.RUNNING
        assert execution.active_lease() == lease
        assert not any(
            event.event_type is FleetRunEventType.RUN_CANCEL_REQUESTED
            for event in owner.runs.events(admitted.fleet_run_id).events
        )

        owner.shutdown(wait_for_runs=False)
        detail = owner.runs.get(admitted.fleet_run_id)
        assert detail.run.status is FleetRunStatus.CANCELLED
        assert detail.outcome is FleetRunOutcome.CANCELLED
        assert execution.active_lease() is None
        assert any(
            event.event_type is FleetRunEventType.DESCENDANTS_QUIESCENT
            for event in owner.runs.events(admitted.fleet_run_id).events
        )
    finally:
        release.touch()
        if observer is not None:
            observer.shutdown(wait_for_runs=False)
        owner.shutdown(wait_for_runs=False)
