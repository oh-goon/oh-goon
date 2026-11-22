"""Selected native slice/scope/descriptor custody, separate from model quality."""

from __future__ import annotations

import json
import os
import select
import sys
import time

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.run_resources import RunConcurrencyPolicy, RunResourceEnvelopeEvidence
from oh_goon.engine.atomic_execution_bytes import seal_execution_bytes
from oh_goon.engine.command import run_command
from oh_goon.integrations.run_resource_envelope import (
    DeferredWorkerResourceScope,
    capture_native_runtime_budget,
    finish_worker_resource_scope,
    pin_started_run_resource_scope,
    prepare_worker_resource_scope,
    recover_worker_resource_scope,
)

pytestmark = [pytest.mark.certification, pytest.mark.process, pytest.mark.no_resume]


@pytest.mark.parametrize("mode", ["success", "private-memory-oom"])
def test_native_deferred_worker_keeps_sealed_descriptors_and_original_scope_witness(tmp_path, mode):
    selected = os.environ.get("OH_GOON_NATIVE_CONCURRENCY_POLICY")
    if selected is None:
        pytest.skip("Select an explicit native aggregate qualification environment")
    policy = RunConcurrencyPolicy.model_validate_json(selected)
    budget = capture_native_runtime_budget(policy)
    deferred = prepare_worker_resource_scope("native-resource-probe", budget)
    path = tmp_path / "protected-scope-intent.json"
    path.write_text(json.dumps(deferred.to_payload()))
    path.chmod(0o400)
    sealed = seal_execution_bytes(path, label="native worker scope probe")
    path.unlink()
    ready_read, ready_write = os.pipe()
    ack_read, ack_write = os.pipe()
    pinned = None
    observed = None
    leader_matches_worker = None
    program = """import json,os,sys
from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope,observe_worker_resource_scope,verify_worker_resource_claim
descriptor,ready,ack=map(int,sys.argv[1:4])
intent=DeferredWorkerResourceScope.from_payload(json.loads(os.read(descriptor,65536)))
os.close(descriptor)
envelope=observe_worker_resource_scope(intent,handoff_sha256=sys.argv[4])
os.write(ready,envelope.model_dump_json().encode()+b'\\n')
os.close(ready)
assert os.read(ack,1)==b'1'
os.close(ack)
claim=verify_worker_resource_claim(intent,envelope)
claim.require_matches(intent.admission,envelope)
print('native scope and sealed descriptor verified',flush=True)
"""
    if mode == "private-memory-oom":
        assert policy.worker_resources.memory_bytes is not None
        program += f"payload=bytearray({policy.worker_resources.memory_bytes + 16 * 1024 * 1024})\n"

    def observe(event):
        nonlocal pinned, observed, leader_matches_worker
        if event.kind != "PROCESS_STARTED":
            return
        assert select.select([ready_read], [], [], 15)[0], "Native worker scope readiness missing"
        data = os.read(ready_read, 65536)
        observed = RunResourceEnvelopeEvidence.model_validate_json(data)
        leader_matches_worker = event.pid == observed.process_birth.pid
        pinned = pin_started_run_resource_scope(
            deferred.scope,
            observed,
            expected_birth=observed.process_birth,
            handoff_sha256=sealed.identity,
        )
        os.write(ack_write, b"1")

    try:
        result = run_command(
            *deferred.scope.command(
                sealed.identity,
                (
                    sys.executable,
                    "-I",
                    "-c",
                    program,
                    str(sealed.descriptor),
                    str(ready_write),
                    str(ack_read),
                    sealed.identity,
                ),
            ),
            pass_fds=(sealed.descriptor, ready_write, ack_read),
            process_observer=observe,
            timeout=25,
            max_capture_bytes_per_stream=16384,
            require_process_group_quiescence=True,
        )
        if mode == "success":
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, "Private worker memory ceiling did not fail the workload"
        assert "sealed descriptor verified" in result.stdout
        assert pinned is not None and observed is not None
        if mode == "private-memory-oom":
            assert budget.journalctl is not None
            # A manager terminal accounting event does not authorize release of
            # a retained failed scope. The independent native unit/kernel joins
            # remain necessary even when this structured record is available.
            deadline = time.monotonic() + 5
            native_records = []
            while time.monotonic() < deadline:
                journal = run_command(
                    budget.journalctl,
                    "--user",
                    "--quiet",
                    "--no-pager",
                    "--output=json",
                    "--output-fields=MESSAGE_ID,USER_UNIT,USER_INVOCATION_ID",
                    "--lines=2",
                    f"USER_UNIT={observed.unit_name}",
                    f"USER_INVOCATION_ID={observed.invocation_id}",
                    "MESSAGE_ID=ae8f7b866b0347b9af31fe1c80b127c0",
                    env=budget.environment,
                    timeout=5,
                    max_capture_bytes_per_stream=8192,
                )
                native_records = [json.loads(line) for line in journal.stdout.splitlines()]
                if native_records:
                    break
                time.sleep(0.05)
            assert len(native_records) == 1
            with pytest.raises(FleetPlanInvalid, match="retained or active"):
                recover_worker_resource_scope(deferred, observed)
        closure = finish_worker_resource_scope(deferred, observed, pinned=pinned)
        closure.require_matches(deferred.admission, observed)
        assert closure.observation.cleanup == "VERIFIED"
        print(
            json.dumps(
                {
                    "worker_subject": observed.subject,
                    "worker_unit": observed.unit_name,
                    "worker_invocation": observed.invocation_id,
                    "aggregate_unit": budget.aggregate.unit_name,
                    "aggregate_invocation": budget.aggregate.invocation_id,
                    "command_leader_matches_worker": leader_matches_worker,
                    "original_scope_cleanup": closure.observation.cleanup,
                    "mode": mode,
                }
            )
        )
    finally:
        if pinned is not None:
            pinned.close()
        for descriptor in (sealed.descriptor, ready_read, ready_write, ack_read, ack_write):
            os.close(descriptor)


def test_native_original_manager_witness_survives_controller_crash_without_scope_pin(tmp_path):
    selected = os.environ.get("OH_GOON_NATIVE_CONCURRENCY_POLICY")
    if selected is None:
        pytest.skip("Select an explicit native aggregate qualification environment")
    RunConcurrencyPolicy.model_validate_json(selected)
    receipt, release, errors = (
        tmp_path / name for name in ("resource-receipt.json", "release", "worker-errors")
    )
    worker = """import json,os,sys,time
from pathlib import Path
from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope,observe_worker_resource_scope,verify_worker_resource_claim
intent=DeferredWorkerResourceScope.from_payload(json.loads(os.read(int(sys.argv[1]),65536)))
os.close(int(sys.argv[1]))
envelope=observe_worker_resource_scope(intent,handoff_sha256=sys.argv[2])
verify_worker_resource_claim(intent,envelope).require_matches(intent.admission,envelope)
Path(sys.argv[3]).write_text(json.dumps({'deferred':intent.to_payload(),'envelope':envelope.model_dump(mode='json')}))
deadline=time.monotonic()+20
while not Path(sys.argv[4]).exists():
    if time.monotonic()>deadline:raise TimeoutError('explicit native probe release missing')
    time.sleep(.01)
"""
    controller = """import json,os,signal,subprocess,sys,time
from pathlib import Path
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.engine.atomic_execution_bytes import seal_execution_bytes
from oh_goon.integrations.run_resource_envelope import capture_native_runtime_budget,prepare_worker_resource_scope
policy=RunConcurrencyPolicy.model_validate_json(os.environ['OH_GOON_NATIVE_CONCURRENCY_POLICY'])
intent=prepare_worker_resource_scope('native-restart-probe',capture_native_runtime_budget(policy))
path=Path(sys.argv[1]).with_suffix('.intent')
path.write_text(json.dumps(intent.to_payload()));path.chmod(0o400)
sealed=seal_execution_bytes(path,label='native restart resource probe');path.unlink()
with open(sys.argv[3],'wb') as errors:
    subprocess.Popen(intent.scope.command(sealed.identity,(sys.executable,'-I','-c',sys.argv[4],str(sealed.descriptor),sealed.identity,sys.argv[1],sys.argv[2])),pass_fds=(sealed.descriptor,),start_new_session=True,stdout=subprocess.DEVNULL,stderr=errors)
os.close(sealed.descriptor)
deadline=time.monotonic()+15
while not Path(sys.argv[1]).exists():
    if time.monotonic()>deadline:raise TimeoutError('native restart probe readiness missing')
    time.sleep(.01)
os.kill(os.getpid(),signal.SIGKILL)
"""
    result = run_command(
        sys.executable,
        "-I",
        "-c",
        controller,
        str(receipt),
        str(release),
        str(errors),
        worker,
        timeout=20,
        max_capture_bytes_per_stream=16384,
        require_process_group_quiescence=True,
    )
    assert result.returncode == -9, result.stderr + (errors.read_text() if errors.exists() else "")
    recorded = json.loads(receipt.read_text())
    deferred = DeferredWorkerResourceScope.from_payload(recorded["deferred"])
    envelope = RunResourceEnvelopeEvidence.model_validate(recorded["envelope"])
    # A controller exit alone cannot release a still-populated original scope.
    with pytest.raises(FleetPlanInvalid):
        recover_worker_resource_scope(deferred, envelope)
    release.touch()
    deadline = time.monotonic() + 15
    closure = None
    last = None
    while time.monotonic() < deadline:
        try:
            closure = recover_worker_resource_scope(deferred, envelope)
            break
        except FleetPlanInvalid as error:
            last = f"{type(error).__name__}: {error}"
            time.sleep(0.05)
    assert closure is not None, f"Native restart witness unavailable: {last}; {errors.read_text()}"
    closure.require_matches(deferred.admission, envelope)
    assert closure.observation.native_witness == "original_systemd_journal"
    assert closure.observation.native_witness_identity
    assert closure.observation.state == "INCOMPLETE"  # no manufactured workload success
    print(
        json.dumps(
            {
                "worker_unit": envelope.unit_name,
                "worker_invocation": envelope.invocation_id,
                "manager_version": deferred.budget.aggregate.manager_version,
                "native_witness": closure.observation.native_witness,
                "native_witness_identity": closure.observation.native_witness_identity,
                "cleanup": closure.observation.cleanup,
            }
        )
    )
