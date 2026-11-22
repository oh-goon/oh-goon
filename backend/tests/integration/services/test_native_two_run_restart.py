"""Original claimed Runs retain separate custody across a controller SIGKILL."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_events import RunResourcesObservedEvent
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.core.process_birth import observe_process_birth, observe_process_liveness
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.run_resource_envelope import (
    DeferredWorkerResourceScope,
    VerifiedWorkerResourceClosure,
    recover_worker_resource_scope,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.execution.sqlite_lease import WorkerResourceReservation
from oh_goon.runtime.journal import Journal
from tests.factories.native_run_concurrency import selected_native_concurrency_environment
from tests.factories.native_two_run import PAIR_VERIFICATION_ARGV, native_two_run_provider

pytestmark = [pytest.mark.certification, pytest.mark.process, pytest.mark.no_resume]
_POLICY_ENV = "OH_GOON_HUG223_RESTART_POLICY"
_CONTROLLER = """import json,shutil,signal,sys
from pydantic import TypeAdapter
from pathlib import Path
from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
data=json.loads(Path(sys.argv[1]).read_text())
runtime=ApplicationRuntime.build(standard_local=True,standard_process_isolation=BubblewrapProcessIsolation(shutil.which('bwrap') or ''),run_concurrency=RunConcurrencyPolicy.model_validate(data['policy']))
runtime.locations.add(Path(data['repository_parent']))
ids=[]
for label in ('a','b'):
    run=runtime.plans.run_native_agent(selected=TypeAdapter(ResolvedNativeAgent).validate_python(data['selected'][label]),workspace=Path(data['repositories'][label]),skill=None,task=f'HUG-223 owner {label}: edit only this candidate and pass its independent verifier',candidate=True,fleet_run_id='two-run-'+label,verification=(tuple(data['verification']),))
    ids.append(run.fleet_run_id)
Path(sys.argv[2]).write_text(json.dumps(ids))
while True:signal.pause()
"""


def _wait(predicate: Callable[[], bool], *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Original native Run did not reach the expected cut"
        time.sleep(0.05)


def _reservation(database: LocalDatabase, run_id: str) -> WorkerResourceReservation | None:
    with database.transaction() as connection:
        return ExecutionCoordinator(database).worker_resource_reservation_in(connection, run_id)


def _closure(reservation: WorkerResourceReservation) -> VerifiedWorkerResourceClosure:
    assert reservation.deferred_resource_scope is not None and reservation.envelope is not None
    deferred = DeferredWorkerResourceScope.from_payload(reservation.deferred_resource_scope)
    deadline = time.monotonic() + 15
    while True:
        try:
            return recover_worker_resource_scope(deferred, reservation.envelope)
        except FleetPlanInvalid:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def test_original_two_native_runs_finish_after_controller_crash_and_reconcile_separately(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    assert os.environ.get("OH_GOON_NATIVE_PI") and os.environ.get(
        "OH_GOON_NATIVE_PI_RUNTIME_ROOT"
    ), "Select the actual public installed Pi implementation and its complete runtime root"
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
    with native_two_run_provider(
        tmp_path, policy, program=program, public_runtime=public_runtime
    ) as provider:
        input_path, ready_path = tmp_path / "controller-input.json", tmp_path / "controller-ready"
        input_path.write_text(
            json.dumps(
                {
                    "policy": policy.model_dump(mode="json"),
                    "repository_parent": str(tmp_path / "repositories"),
                    "repositories": {
                        key: str(value) for key, value in provider.repositories.items()
                    },
                    "selected": {
                        key: TypeAdapter(ResolvedNativeAgent).dump_python(value, mode="json")
                        for key, value in provider.selected.items()
                    },
                    "verification": PAIR_VERIFICATION_ARGV,
                }
            )
        )
        runtime = None
        with (tmp_path / "original-controller.log").open("wb") as output:
            controller = subprocess.Popen(
                (sys.executable, "-I", "-c", _CONTROLLER, str(input_path), str(ready_path)),
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
            try:
                for label in ("a", "b"):
                    assert provider.entered[label].wait(60), (
                        tmp_path / "original-controller.log"
                    ).read_text()
                _wait(ready_path.exists)
                assert json.loads(ready_path.read_text()) == ["two-run-a", "two-run-b"]
                controller_birth = observe_process_birth(controller.pid)
                assert controller_birth is not None
                database = LocalDatabase(provider.database_path, read_only=True)
                journal = Journal(database)
                original = {
                    label: _reservation(database, "two-run-" + label) for label in ("a", "b")
                }
                for reservation in original.values():
                    assert reservation is not None and reservation.envelope is not None
                    assert reservation.controller.owner_pid == controller.pid
                    assert reservation.lease.custody is not None
                    assert reservation.lease.custody.phase == "WORKER_OWNED"
                assert original["a"] is not None and original["b"] is not None
                assert original["a"].lease.token != original["b"].lease.token
                assert original["a"].envelope != original["b"].envelope
                os.kill(controller.pid, signal.SIGKILL)
                assert controller.wait(timeout=10) == -signal.SIGKILL
                assert observe_process_liveness(controller_birth.pid) is False
                for reservation in original.values():
                    assert reservation is not None and reservation.envelope is not None
                    assert observe_process_liveness(reservation.envelope.process_birth.pid) is True

                runtime = ApplicationRuntime.build(
                    standard_local=True,
                    standard_process_isolation=BubblewrapProcessIsolation(
                        shutil.which("bwrap") or ""
                    ),
                    run_concurrency=policy,
                )
                for label in ("a", "b"):
                    assert not runtime.runs.get("two-run-" + label).run.status.terminal
                provider.release["a"].set()
                provider.finish["a"].set()
                _wait(
                    lambda: journal.read_view().required_run_detail("two-run-a").run.status.terminal
                )
                a_closing = _reservation(database, "two-run-a")
                assert a_closing is not None and a_closing.lease.custody is not None
                assert a_closing.lease.custody.phase == "RESOURCE_RESERVED"
                a_proof = _closure(a_closing)
                assert a_proof.observation.state == "INCOMPLETE"
                assert a_proof.observation.cleanup == "VERIFIED"
                with pytest.raises(FleetPlanInvalid):
                    a_proof.require_matches(original["b"].admission, original["b"].envelope)
                runtime.recovery.recover_worker_exit("two-run-a")
                a_done = runtime.runs.get("two-run-a")
                assert a_done.outcome.value == "SUCCESS", a_done.model_dump_json()
                assert a_done.resource_observation == a_proof.observation
                assert _reservation(database, "two-run-a") is None
                b_live = _reservation(database, "two-run-b")
                assert b_live == original["b"]
                assert not runtime.runs.get("two-run-b").run.status.terminal
                assert original["b"].envelope is not None
                assert observe_process_liveness(original["b"].envelope.process_birth.pid) is True
                # Lost caller acknowledgement repeats the public original recovery
                # without adding metrics, borrowing B's slot, or duplicating a fact.
                runtime.recovery.recover_worker_exit("two-run-a")
                assert (
                    runtime.runs.get("two-run-a").resource_observation
                    == a_done.resource_observation
                )
                assert _reservation(database, "two-run-b") == original["b"]

                provider.release["b"].set()
                provider.finish["b"].set()
                _wait(
                    lambda: journal.read_view().required_run_detail("two-run-b").run.status.terminal
                )
                b_closing = _reservation(database, "two-run-b")
                assert b_closing is not None
                _closure(b_closing)
                runtime.recovery.recover_worker_exit("two-run-b")
                assert _reservation(database, "two-run-b") is None
                details = [runtime.runs.get("two-run-" + label) for label in ("a", "b")]
                assert [detail.outcome.value for detail in details] == ["SUCCESS", "SUCCESS"]
                for label, detail in zip(("a", "b"), details, strict=True):
                    original_reservation = original[label]
                    assert original_reservation is not None
                    assert detail.worker_resource_envelope == original_reservation.envelope
                    assert detail.resource_observation is not None
                    assert detail.resource_observation.state == "INCOMPLETE"
                    assert detail.resource_observation.cleanup == "VERIFIED"
                    assert detail.resource_observation.native_witness == "original_systemd_journal"
                    assert (
                        len(
                            [
                                event
                                for event in detail.lifecycle_events
                                if isinstance(event, RunResourcesObservedEvent)
                            ]
                        )
                        == 1
                    )
                    assert (
                        (provider.repositories[label] / "code.py")
                        .read_text()
                        .endswith("VALUE = 41\n")
                    )
                    assert provider.facts[label]["owner"] == label
                assert not provider.errors, provider.errors
                print(
                    json.dumps(
                        {
                            "original_controller_sigkill": "PASS",
                            "two_original_worker_results": [
                                detail.outcome.value for detail in details
                            ],
                            "same_manager_restart_closure": "PASS",
                            "closing_a_while_b_live": "PASS",
                            "lost_ack_replay": "PASS",
                            "worker_pids": [
                                reservation.envelope.process_birth.pid
                                for reservation in original.values()
                                if reservation is not None and reservation.envelope is not None
                            ],
                        }
                    ),
                    flush=True,
                )
            finally:
                provider.release_all()
                if controller.poll() is None:
                    controller.kill()
                    controller.wait(timeout=10)
                if runtime is not None:
                    runtime.shutdown(
                        wait_for_runs=False, cancel_grace_seconds=2, containment_timeout_seconds=10
                    )
