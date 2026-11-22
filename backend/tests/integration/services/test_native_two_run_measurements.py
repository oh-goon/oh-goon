"""Named native measurements; no automatic default promotion or model-quality claim."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import PAIR_VERIFICATION_ARGV, native_two_run_case

_POLICY_ENV = "OH_GOON_H223_SELECTED_POLICY"


@pytest.mark.certification
@pytest.mark.no_resume
def test_matched_one_versus_two_native_run_observations(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    selected = os.environ.get(_POLICY_ENV)
    if selected is None:
        with selected_native_concurrency_environment(
            aggregate_resources=CicdResourcePolicy(
                cpu_millis=2000, memory_mib=3072, max_processes=1024
            ),
            worker_resources=CicdResourcePolicy(cpu_millis=750, memory_mib=768, max_processes=256),
            controller_reserve=CicdResourcePolicy(
                cpu_millis=500, memory_mib=1024, max_processes=256
            ),
        ) as environment:
            supplied = dict(os.environ)
            supplied[_POLICY_ENV] = environment.policy.model_dump_json()
            result = environment.run(
                (
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "-m",
                    "certification",
                    str(Path(__file__)) + "::" + request.node.name,
                ),
                cwd=Path(__file__).parents[3],
                environment=supplied,
                timeout=720,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout)
        return

    policy = RunConcurrencyPolicy.model_validate_json(selected)
    program = Path(os.environ["OH_GOON_NATIVE_PI"]).resolve(strict=True)
    public_runtime = Path(os.environ["OH_GOON_NATIVE_PI_RUNTIME_ROOT"]).resolve(strict=True)
    oracle_identity = hashlib.sha256(json.dumps(PAIR_VERIFICATION_ARGV).encode()).hexdigest()
    control = tmp_path / "oracle-negative"
    control.mkdir()
    (control / "code.py").write_bytes(b"VALUE = 41\n")
    negative = subprocess.run(PAIR_VERIFICATION_ARGV, cwd=control, capture_output=True, timeout=10)
    assert negative.returncode != 0
    (control / "code.py").write_bytes(b"VALUE = 42\n")
    positive = subprocess.run(PAIR_VERIFICATION_ARGV, cwd=control, capture_output=True, timeout=10)
    assert positive.returncode == 0

    observations = []
    # Both arms use identical native worker ceilings and aggregate intent.
    # The controlled variable is one-at-a-time versus two in-flight submissions.
    for pair in range(3):
        order = ("one", "two") if pair % 2 == 0 else ("two", "one")
        for mode in order:
            with native_two_run_case(
                tmp_path / f"pair-{pair}-{mode}",
                policy,
                program=program,
                public_runtime=public_runtime,
            ) as case:
                observer = LocalDatabase(Path(os.environ["OH_GOON_DB"]), read_only=True)
                execution = ExecutionCoordinator(observer)
                records = []
                case.release_all()
                started = time.monotonic()

                def wait_closed(
                    run_id: str,
                    observer: LocalDatabase = observer,
                    execution: ExecutionCoordinator = execution,
                ) -> None:
                    deadline = time.monotonic() + 30
                    while True:
                        with observer.transaction() as connection:
                            retained = execution.worker_resource_reservation_in(connection, run_id)
                        if retained is None:
                            return
                        assert time.monotonic() < deadline, (
                            "Original physical reservation did not converge"
                        )
                        time.sleep(0.02)

                if mode == "one":
                    for label in ("a", "b"):
                        run = case.start(label)
                        detail = case.runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=120)
                        assert detail.outcome.value == "SUCCESS", detail.model_dump_json()
                        wait_closed(run.fleet_run_id)
                        records.append(case.runtime.runs.get(run.fleet_run_id))
                else:
                    runs = [case.start(label) for label in ("a", "b")]
                    for run in runs:
                        detail = case.runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=120)
                        assert detail.outcome.value == "SUCCESS", detail.model_dump_json()
                        wait_closed(run.fleet_run_id)
                        records.append(case.runtime.runs.get(run.fleet_run_id))
                elapsed = time.monotonic() - started
                assert not case.errors, case.errors
                assert all(
                    item.resource_observation is not None
                    and item.resource_observation.cleanup == "VERIFIED"
                    for item in records
                )
                row = {
                    "pair": pair,
                    "mode": mode,
                    "elapsed_seconds": elapsed,
                    "verified_tasks": len(records),
                    "verified_tasks_per_hour": 3600 * len(records) / elapsed,
                    "regression_control_misses": 0,
                    "run_ids": [item.run.fleet_run_id for item in records],
                    "worker_pids": [
                        item.worker_resource_envelope.process_birth.pid
                        for item in records
                        if item.worker_resource_envelope
                    ],
                    "oracle_sha256": oracle_identity,
                    "aggregate_resources": policy.aggregate_resources.model_dump(mode="json"),
                    "worker_resources": policy.worker_resources.model_dump(mode="json"),
                    "native_implementation_sha256": case.selected["a"].executable_sha256,
                    "scope": "Controlled real Pi/synthetic-provider pipeline; not live-model quality or billing",
                }
                observations.append(row)
                print("HUG223_TRIAL " + json.dumps(row, sort_keys=True), flush=True)
    print("HUG223_MATCHED_OBSERVATIONS " + json.dumps(observations, sort_keys=True), flush=True)
