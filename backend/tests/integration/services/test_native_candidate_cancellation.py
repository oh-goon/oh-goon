"""Actual cancelled namespace writers retain work until explicit reconciliation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run_events import StepCancelledEvent
from oh_goon.core.vocabulary import FleetRunStatus
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux" or shutil.which("bwrap") is None, reason="Linux Bubblewrap required"
    ),
]


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=repository, capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


def _events(runtime: ApplicationRuntime, run_id: str) -> tuple:
    cursor, retained = 0, []
    for _ in range(8):
        page = runtime.runs.events(run_id, after=cursor, limit=100)
        retained.extend(page.events)
        if not page.has_more:
            return tuple(retained)
        assert page.next_sequence > cursor
        cursor = page.next_sequence
    raise AssertionError("Cancelled fixture exceeded bounded event readback")


@pytest.mark.parametrize("dirty", [False, True], ids=["clean-reconcile", "dirty-retain"])
def test_cancelled_candidate_reconciles_only_canonical_closed_clean_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty: bool
) -> None:
    repository = tmp_path / "developer"
    repository.mkdir()
    (repository / "code.py").write_text("VALUE = 41\n")
    _git(repository, "init", "-q")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state/app.sqlite"))
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / kind.lower()))
    executable = tmp_path / "public-native/pi"
    executable.parent.mkdir()
    ready = "candidate-cancellation-ready"
    executable.write_text(
        "#!/usr/bin/python3\nimport json,pathlib,sys,time\n"
        "data=json.load(sys.stdin)\nassert 'Hashmarks' in data['repository_evidence']\n"
        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        + ("pathlib.Path('code.py').write_text('VALUE = 41\\n')\n" if not dirty else "")
        + "for event in ({'type':'agent_start'},"
        + f"{{'type':'message_end','message':{{'role':'assistant','content':{ready!r},'stopReason':'stop'}}}}): "
        + "print(json.dumps(event),flush=True)\n"
        + "time.sleep(120)\n"
    )
    executable.chmod(0o700)
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate-cancel-fixture",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap", inference_profile="fixture", timeout_seconds=60
        ),
        executable_sha256=native_executable_sha256(executable),
        backend_configuration_id=backend.configuration_identity(),
        connection=AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "fixture": {
                            "api": "openai-chat-completions",
                            "base_url": "http://127.0.0.1:1/v1",
                        }
                    },
                    "models": {"fixture": {"provider": "fixture", "model": "fixture"}},
                }
            ),
            "fixture",
        ),
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
        started = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repository,
            skill=None,
            task="Edit the owned candidate",
            candidate=True,
        )
        run_id = started.fleet_run_id
        deadline = monotonic() + 45
        tick = Event()
        while True:
            live = runtime.runs.game_tape_live_output(run_id)
            if ready in live.model_dump_json():
                break
            assert monotonic() < deadline and live.state != "SEALED", live.model_dump_json()
            tick.wait(0.05)
        assert runtime.runs.cancel(run_id).status.cancel_request_satisfied
        detail = runtime.runs.wait_for_terminal(run_id, timeout=40)
        assert detail.run.status is FleetRunStatus.CANCELLED
        step = detail.game_tape.repositories[0].steps[0]
        assert not step.attempts and step.cancellation is not None
        assert step.settlement is not None and step.settlement.cleanup == "VERIFIED"
        assert step.cancellation.standard_isolation is not None
        scope = step.cancellation.standard_isolation.workspace_writer_scope
        assert scope is not None
        assert scope.physical_execution_id == step.cancellation.physical_execution_id
        original = _events(runtime, run_id)
        assert any(
            isinstance(event, StepCancelledEvent) and event.cancellation == step.cancellation
            for event in original
        )
        inspection = runtime.plans.inspect_candidate_run(run_id)
        assert inspection.receipt is None
        assert inspection.checkpoint == inspection.admission.checkpoint
        assert inspection.state.current_lease_id == scope.materialization.lease_id
        path = Path(inspection.path)
        assert (path / "code.py").read_text() == f"VALUE = {42 if dirty else 41}\n"
        assert (repository / "code.py").read_text() == "VALUE = 41\n"
        assert _git(repository, "status", "--porcelain") == ""
        if dirty:
            with pytest.raises(FleetDomainError, match="dirty|changed"):
                runtime.plans.retire_candidate_run(
                    run_id,
                    expected_state_version=inspection.state.state_version,
                    authorize=True,
                    reconcile=True,
                )
            retained = runtime.plans.inspect_candidate_run(run_id)
            assert retained.state == inspection.state
            assert (path / "code.py").read_text() == "VALUE = 42\n"
            assert retained.source_state.lifecycle_state is WorkspaceLifecycleState.READY
        else:
            retired = runtime.plans.retire_candidate_run(
                run_id,
                expected_state_version=inspection.state.state_version,
                authorize=True,
                reconcile=True,
            )
            assert retired.state.lifecycle_state is WorkspaceLifecycleState.RELEASED
            assert retired.source_state.lifecycle_state is WorkspaceLifecycleState.RELEASED
            assert not path.exists()
        assert _events(runtime, run_id) == original
        reopened = subprocess.run(
            (sys.executable, "-m", "oh_goon", "runs", "candidate", run_id, "--json"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert reopened.returncode == 0, reopened.stderr + reopened.stdout
        assert json.loads(reopened.stdout)["state"]["current_checkpoint_id"] == (
            inspection.admission.checkpoint.checkpoint_id
        )
        assert runtime.runs.get(run_id).run.status is FleetRunStatus.CANCELLED
    finally:
        runtime.shutdown(wait_for_runs=True)
