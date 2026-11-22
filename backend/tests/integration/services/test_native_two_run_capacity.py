"""Selected native two-Run qualification; serial product defaults stay unchanged."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import native_two_run_case

_POLICY_ENV = "OH_GOON_HUG223_SELECTED_POLICY"


@pytest.mark.certification
@pytest.mark.no_resume
def test_two_real_native_runs_share_one_runtime_and_refuse_third(tmp_path: Path, request) -> None:
    if not os.environ.get("OH_GOON_NATIVE_PI") or not os.environ.get(
        "OH_GOON_NATIVE_PI_RUNTIME_ROOT"
    ):
        pytest.fail(
            "Select the public installed Pi executable and complete runtime root explicitly"
        )
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
            child_environment = dict(os.environ)
            child_environment[_POLICY_ENV] = environment.policy.model_dump_json()
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
                environment=child_environment,
                timeout=240,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout)
        return
    policy = RunConcurrencyPolicy.model_validate_json(selected)
    program = Path(os.environ["OH_GOON_NATIVE_PI"]).resolve(strict=True)
    public_runtime = Path(os.environ["OH_GOON_NATIVE_PI_RUNTIME_ROOT"]).resolve(strict=True)
    with native_two_run_case(
        tmp_path, policy, program=program, public_runtime=public_runtime
    ) as case:
        a = case.start("a")
        assert case.entered["a"].wait(45), case.runtime.runs.game_tape_live_output(
            a.fleet_run_id
        ).model_dump_json()
        b = case.start("b")
        assert case.entered["b"].wait(45), case.runtime.runs.game_tape_live_output(
            b.fleet_run_id
        ).model_dump_json()
        print("HUG223_BOTH_NATIVE_READY", flush=True)
        active = case.active_reservations()
        envelopes = []
        for value in active.values():
            assert value.envelope is not None
            assert value.lease.custody is not None and value.lease.custody.phase == "WORKER_OWNED"
            envelopes.append(value.envelope)
        assert len({value.process_birth.pid for value in envelopes}) == 2
        assert os.getpid() not in {value.process_birth.pid for value in envelopes}
        assert len({value.unit_name for value in envelopes}) == 2
        assert len({value.invocation_id for value in envelopes}) == 2
        assert len({value.admission.budget_identity for value in active.values()}) == 1
        with pytest.raises(ExecutionBusy):
            case.start("third")
        case.release_all()
        results = [
            case.runtime.runs.wait_for_terminal(run.fleet_run_id, timeout=90) for run in (a, b)
        ]
        assert [result.outcome.value for result in results] == ["SUCCESS", "SUCCESS"], [
            result.model_dump_json() for result in results
        ]
        assert not case.errors, case.errors
        assert all(
            result.resource_observation is not None
            and result.resource_observation.cleanup == "VERIFIED"
            for result in results
        )
        for label in ("a", "b"):
            assert (case.repositories[label] / "code.py").read_text().endswith("VALUE = 41\n")
            assert case.facts[label]["owner"] == label
        print(
            json.dumps(
                {
                    "two_original_runs": "PASS",
                    "run_ids": [r.run.fleet_run_id for r in results],
                    "worker_pids": [envelope.process_birth.pid for envelope in envelopes],
                    "candidate_roots": [case.facts[label]["cwd"] for label in ("a", "b")],
                }
            ),
            flush=True,
        )
