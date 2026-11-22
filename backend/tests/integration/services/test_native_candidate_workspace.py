"""Actual claimed candidate writer, checkpoint, dirty retention and native retirement.

The Pi protocol fixture qualifies product wiring; the real Bubblewrap/kernel
boundary supplies physical namespace evidence, not installed-model qualification.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

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
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.database import LocalDatabase
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
        ("git", *args), cwd=repository, text=True, capture_output=True, check=True, timeout=10
    ).stdout.strip()


def _run_events(runtime: ApplicationRuntime, run_id: str) -> tuple:
    cursor, entries = 0, []
    for _page in range(8):
        page = runtime.runs.events(run_id, after=cursor, limit=100)
        entries.extend(page.events)
        if not page.has_more:
            return tuple(entries)
        assert page.next_sequence > cursor
        cursor = page.next_sequence
    raise AssertionError("Fixture Run exceeded its bounded event readback")


@pytest.mark.parametrize("mode", ["clean-retire", "dirty-retain", "ignored-output"])
def test_native_candidate_is_the_only_writer_and_retirement_preserves_unpromoted_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    dirty = mode == "dirty-retain"
    ignored_output = mode == "ignored-output"
    repository = tmp_path / "developer"
    repository.mkdir()
    (repository / "code.py").write_text("VALUE = 41\n")
    (repository / ".gitignore").write_text("frontend/dist/\n")
    (repository / "frontend").mkdir()
    (repository / "frontend/README.md").write_text("# Fixture frontend\n")
    (repository / "verify.py").write_text(
        f"from code import VALUE\nassert VALUE == {42 if dirty else 41}\n"
    )
    _git(repository, "init", "-q")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Candidate Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    revision = _git(repository, "rev-parse", "HEAD")
    database_path = tmp_path / "state" / "app.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database_path))
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / kind.lower()))
    database = LocalDatabase(database_path)
    workspaces = RepositoryWorkspaceAuthority(database)
    baseline = workspaces.request(
        repository_id=repository_id(repository),
        base_revision=revision,
        kind=WorkspaceKind.READ_SNAPSHOT,
    )
    allocated = workspaces.allocate(baseline.workspace_id, expected_state_version=0)
    _, source = workspaces.materialize_read_snapshot(
        baseline, repository, expected_state_version=allocated.state_version
    )
    other = workspaces.request(
        repository_id=repository_id(repository), base_revision=revision, kind=WorkspaceKind.WORKTREE
    )
    allocated = workspaces.allocate(other.workspace_id, expected_state_version=0)
    workspaces.materialize_worktree(
        other, repository, source, expected_state_version=allocated.state_version
    )
    other_path = workspaces.physical_path(other)
    public = tmp_path / "public-native"
    public.mkdir()
    executable = public / "pi"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json,pathlib,sys\n"
        "data=json.load(sys.stdin)\n"
        "assert 'Hashmarks' in data['repository_evidence']\n"
        "assert '--no-session' in sys.argv\n"
        "assert pathlib.Path(data['captured_repository'],'code.py').read_text()=='VALUE = 41\\n'\n"
        f"assert pathlib.Path.cwd()!=pathlib.Path({str(repository)!r})\n"
        f"for forbidden in ({str(repository / 'code.py')!r},{str(other_path / 'code.py')!r}):\n"
        "    try: pathlib.Path(forbidden).write_text('ESCAPED')\n"
        "    except OSError: pass\n"
        "    else: raise AssertionError('foreign workspace became writable')\n"
        "try: metadata=pathlib.Path('.git').read_text()\n"
        "except OSError: metadata=''\n"
        "assert 'gitdir:' not in metadata\n"
        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        + ("pathlib.Path('code.py').write_text('VALUE = 41\\n')\n" if not dirty else "")
        + (
            "pathlib.Path('frontend/dist').mkdir(parents=True)\n"
            "pathlib.Path('frontend/dist/result.bin').write_bytes(b'unpromoted result')\n"
            if ignored_output
            else ""
        )
        + "for event in ({'type':'agent_start'},{'type':'message_end','message':{'role':'assistant','content':'Candidate edited','stopReason':'stop'}},{'type':'agent_settled','aborted':False}): print(json.dumps(event),flush=True)\n"
    )
    executable.chmod(0o700)
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate-fixture",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap", inference_profile="fixture", timeout_seconds=20
        ),
        executable_sha256=native_executable_sha256(executable),
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
        backend_configuration_id=backend.configuration_identity(),
    )
    runner = native_profile_command_runner(
        repository, selected, backend, workspaces.pin_repository_subject
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
            fleet_run_id="candidate-native-run",
            verification=((sys.executable, "-B", "verify.py"),),
        )
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)
        assert detail.outcome.value == "SUCCESS", runtime.runs.game_tape_live_output(
            started.fleet_run_id
        ).model_dump_json()
        assert detail.run.targets[0].path == repository
        assert detail.run.targets[0].repository_id == repository_id(repository)
        operation = detail.operations[0]
        assert operation.resource_settlement == "SETTLED"
        assert isinstance(operation.evidence, FleetPlanOperationEvidence)
        receipt = operation.evidence.candidate_receipt
        assert receipt is not None and receipt.settlement == "checkpointed"
        assert receipt.acceptance is not None and receipt.checkpoint is not None
        assert receipt.assignment.attempt_id == operation.provenance.operation_id
        assert receipt.materialization.lease_id == receipt.lease.lease_id
        assert receipt.state.current_lease_id is None
        scopes = [
            step.attempts[0].standard_isolation.workspace_writer_scope
            for step in operation.evidence.plan.steps
        ]
        assert len(scopes) == 2 and all(scope is not None for scope in scopes)
        assert all(
            scope.materialization.materialization_id == receipt.materialization.materialization_id
            for scope in scopes
        )
        inspection = runtime.plans.inspect_candidate_run(started.fleet_run_id)
        candidate_path = Path(inspection.path)
        assert candidate_path != repository and candidate_path != other_path
        assert (
            (repository / "code.py").read_text()
            == (other_path / "code.py").read_text()
            == "VALUE = 41\n"
        )
        assert _git(repository, "status", "--porcelain") == ""
        assert (candidate_path / "code.py").read_text() == f"VALUE = {42 if dirty else 41}\n"
        original_events = _run_events(runtime, started.fleet_run_id)
        if dirty or ignored_output:
            with pytest.raises(FleetDomainError):
                runtime.plans.retire_candidate_run(
                    started.fleet_run_id,
                    expected_state_version=inspection.state.state_version,
                    authorize=True,
                )
            assert (candidate_path / "code.py").read_text() == f"VALUE = {42 if dirty else 41}\n"
            if ignored_output:
                assert (
                    candidate_path / "frontend/dist/result.bin"
                ).read_bytes() == b"unpromoted result"
                assert (
                    inspection.checkpoint.source_sha256
                    == inspection.admission.checkpoint.source_sha256
                )
            assert workspaces.physical_path(inspection.admission.source_workspace).is_dir()
        else:
            retired = runtime.plans.retire_candidate_run(
                started.fleet_run_id,
                expected_state_version=inspection.state.state_version,
                authorize=True,
            )
            assert retired.state.lifecycle_state is WorkspaceLifecycleState.RELEASED
            assert retired.source_state.lifecycle_state is WorkspaceLifecycleState.RELEASED
            assert not candidate_path.exists()
            assert str(candidate_path) not in _git(repository, "worktree", "list", "--porcelain")
        assert _run_events(runtime, started.fleet_run_id) == original_events
        readback = subprocess.run(
            (sys.executable, "-m", "oh_goon", "runs", "candidate", started.fleet_run_id, "--json"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert readback.returncode == 0, readback.stderr + readback.stdout
        reopened = json.loads(readback.stdout)
        assert reopened["admission"]["workspace"]["workspace_id"] == receipt.workspace.workspace_id
        assert reopened["state"]["current_checkpoint_id"] == receipt.checkpoint.checkpoint_id
    finally:
        runtime.shutdown(wait_for_runs=True)
        state = workspaces.state(other.workspace_id)
        finalized = workspaces.transition(
            other.workspace_id,
            expected_state_version=state.state_version,
            next_state=WorkspaceLifecycleState.FINALIZED,
        )
        workspaces.retire_worktree(other, expected_state_version=finalized.state_version)
        state = workspaces.state(baseline.workspace_id)
        finalized = workspaces.transition(
            baseline.workspace_id,
            expected_state_version=state.state_version,
            next_state=WorkspaceLifecycleState.FINALIZED,
        )
        workspaces.release(baseline.workspace_id, expected_state_version=finalized.state_version)
