"""Controlled native detachment through the original claimed Run and namespace.

This is process/protocol conformance, not installed harness qualification.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run_events import ProcessStartedEvent, ProcessStartingEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.process_birth import observe_process_birth, observe_process_state
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.fakes.native_lifetime import bind_native_descendant


def _native_root(
    runtime: ApplicationRuntime, run_id: str, step_id: str, executable: Path
) -> ProcessStartedEvent:
    deadline = monotonic() + 15
    while True:
        for event in reversed(runtime.runs.events(run_id).events):
            if type(event) is not ProcessStartedEvent or event.step_id != step_id:
                continue
            try:
                descriptor = os.pidfd_open(event.pid)
            except ProcessLookupError:
                continue
            try:
                birth = observe_process_birth(event.pid)
                if (
                    birth is None
                    or event.process_identity != f"{birth.boot_id}:{birth.start_ticks}"
                ):
                    continue
                if select.select([descriptor], [], [], 0)[0]:
                    continue
                with (Path("/proc") / str(event.pid) / "cmdline").open("rb") as source:
                    arguments = source.read(65537)
                assert len(arguments) <= 65536
                parts = arguments.split(b"\0")
                separators = [index for index, value in enumerate(parts) if value == b"--"]
                workload = parts[separators[-1] + 1 :] if separators else parts
                if any(Path(value.decode()).name == executable.name for value in workload if value):
                    return event
            except FileNotFoundError:
                continue
            finally:
                os.close(descriptor)
        assert monotonic() < deadline, "original native Step process did not become observable"
        Event().wait(0.01)


@pytest.mark.process
@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None,
    reason="Linux Bubblewrap and pidfds required",
)
@pytest.mark.parametrize(
    "finish,escaped",
    (("completion", False), ("completion", True), ("cancel", True), ("deadline", True)),
    ids=(
        "completion-same-group",
        "completion-escaped-session",
        "cancel-escaped-session",
        "deadline-escaped-session",
    ),
)
def test_native_child_cannot_outlive_original_claimed_run_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str, escaped: bool
) -> None:
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    source = repository / "source.py"
    source.write_text("VALUE = 42\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    token = "oh-goon-native-child-" + uuid4().hex
    child_program = (
        "import os,signal,sys,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "ready=int(sys.argv[2]);os.write(ready,b'R');os.close(ready)\n"
        "time.sleep(120)\n"
    )
    executable = tmp_path / "native-detached-protocol"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,signal,subprocess,sys\n"
        "prompt=json.load(sys.stdin)\n"
        "assert 'Hashmarks' in prompt['repository_evidence']\n"
        "reader,writer=os.pipe()\n"
        f"child=subprocess.Popen([sys.executable,'-I','-c',{child_program!r},{token!r},str(writer)],"
        f"pass_fds=(writer,),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session={escaped!r})\n"
        "os.close(writer);assert os.read(reader,1)==b'R';os.close(reader)\n"
        "os.kill(os.getpid(),signal.SIGSTOP)\n"
        "record={'schema':'oh-goon.agent-review/v1',**prompt['identity'],'verdict':'CLEAN','summary':'Parent completed; child lifetime is not a result claim','reviewed_paths':{'complete_scope':prompt['identity']['scope_identity']},'findings':[]}\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(record)}}),flush=True)\n"
        "print(json.dumps({'type':'turn.completed'}),flush=True)\n"
    )
    executable.chmod(0o700)
    for name, relative in (
        ("OH_GOON_DB", "state/app.sqlite"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(name, str(tmp_path / relative))
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    connection = AgentConnection.select(
        AgentProvidersConfig.model_validate(
            {
                "providers": {
                    "fixture": {"api": "openai-responses", "base_url": "http://127.0.0.1:1/v1"}
                },
                "models": {"fixture": {"provider": "fixture", "model": "fixture"}},
            }
        ),
        "fixture",
    )
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="detached-lifetime-fixture",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            inference_profile="fixture",
            timeout_seconds=8 if finish == "deadline" else 30,
        ),
        executable_sha256=native_executable_sha256(executable),
        connection=connection,
        backend_configuration_id=backend.configuration_identity(),
    )
    runner = native_profile_command_runner(
        repository, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    runtime = ApplicationRuntime.build(
        standard_local=True,
        standard_process_isolation=backend,
        git_service=GitService(
            command_runner=runner, observation_runner=partial(runner, read_only_workspace=True)
        ),
    )
    try:
        runtime.locations.add(repository)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repository,
            skill=None,
            task="Review captured source",
            role="review",
        )
        assert isinstance(admitted.target, FleetPlanTarget)
        step_id = admitted.target.plan.steps[0].step_id
        root_event = _native_root(runtime, admitted.fleet_run_id, step_id, executable)
        with bind_native_descendant(root_event, token) as observed:
            assert (observed.child_pgid != observed.parent_pgid) is escaped
            stopped_by = monotonic() + 5
            while observe_process_state(observed.parent.pid) != "T":
                assert not select.select([observed.parent_fd], [], [], 0)[0]
                assert monotonic() < stopped_by, "native parent did not acknowledge its gate"
                Event().wait(0.001)
            assert not select.select([observed.child_fd], [], [], 0)[0]
            assert runtime.runs.get(admitted.fleet_run_id).run.status is FleetRunStatus.RUNNING
            if finish == "completion":
                signal.pidfd_send_signal(observed.parent_fd, signal.SIGCONT)
            elif finish == "cancel":
                cancelled = runtime.runs.cancel(admitted.fleet_run_id)
                assert cancelled.status.cancel_request_satisfied
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=40)
            assert select.select([observed.child_fd], [], [], 0)[0], (
                "escaped child survived original owner terminal settlement"
            )
            assert (detail.outcome is FleetRunOutcome.SUCCESS) is (finish == "completion"), (
                runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[-8192:]
            )
            step = detail.game_tape.repositories[0].steps[0]
            assert step.settlement is not None
            assert (
                step.settlement.outcome
                == {"completion": "SUCCESS", "cancel": "CANCELLED", "deadline": "TIMED_OUT"}[finish]
            )
            assert step.settlement.cleanup == "VERIFIED"
            if finish == "cancel":
                assert detail.run.status is FleetRunStatus.CANCELLED
                assert not step.attempts and step.cancellation is not None
                supervisor = step.cancellation.supervisor
            else:
                assert len(step.attempts) == 1
                supervisor = step.attempts[0].supervisor
            assert supervisor is not None and supervisor.final_process_group_quiescent
            assert (
                supervisor.cleanup_reason
                == {"completion": "none", "cancel": "cancel", "deadline": "timeout"}[finish]
            )
            events = runtime.runs.events(admitted.fleet_run_id).events
            process_events = [
                event
                for event in events
                if isinstance(event, ProcessStartingEvent) and event.step_id == step_id
            ]
            starts = [event for event in process_events if type(event) is ProcessStartedEvent]
            started_ids = {event.physical_execution_id for event in starts}
            assert len(started_ids) == len(starts)
            assert [
                event
                for event in starts
                if event.physical_execution_id == root_event.physical_execution_id
            ] == [root_event]
            terminal = [
                event
                for event in events
                if event.event_type
                in {
                    FleetRunEventType.STEP_SUCCEEDED,
                    FleetRunEventType.STEP_FAILED,
                    FleetRunEventType.STEP_CANCELLED,
                }
            ]
            assert len(terminal) == 1
            assert (
                terminal[0].event_type
                is {
                    "completion": FleetRunEventType.STEP_SUCCEEDED,
                    "cancel": FleetRunEventType.STEP_CANCELLED,
                    "deadline": FleetRunEventType.STEP_FAILED,
                }[finish]
            )
            for kind in (FleetRunEventType.PROCESS_EXITED, FleetRunEventType.DESCENDANTS_QUIESCENT):
                physical = [event for event in process_events if event.event_type is kind]
                assert len(physical) == len(started_ids)
                assert {event.physical_execution_id for event in physical} == started_ids
                assert max(event.sequence for event in physical) < terminal[0].sequence
            assert (
                sum(
                    event.event_type
                    in {
                        FleetRunEventType.RUN_COMPLETED,
                        FleetRunEventType.RUN_FAILED,
                        FleetRunEventType.RUN_CANCELLED,
                    }
                    for event in events
                )
                == 1
            )
            assert source.read_text() == "VALUE = 42\n"
    finally:
        runtime.shutdown(wait_for_runs=False, cancel_grace_seconds=1, containment_timeout_seconds=5)

    reopened = ApplicationRuntime.build(standard_local=True, startup_purpose="observation")
    try:
        retained = reopened.runs.get(admitted.fleet_run_id)
        assert retained.operations == detail.operations
        assert reopened.runs.events(admitted.fleet_run_id).events == events
    finally:
        reopened.shutdown(wait_for_runs=True)
