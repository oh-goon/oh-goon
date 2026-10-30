from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

import pytest

import oh_goon.cli_run_resources as resource_cli
from oh_goon.cli_run_resources import (
    PreparedRunResourceHandoff,
    ResolvedCliState,
    RunResourceSignalForwarder,
    prepare_run_resource_handoff,
    restore_run_resource_handoff,
)
from oh_goon.core.errors import ExecutionAuthorityDivergence, RunConfigurationInvalid
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.vocabulary import FleetRunStatus, FleetRunTerminalIntent
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.command import CommandProcessLifecycleEvent, run_command
from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal, RunResourceObservationContext
from oh_goon.services.run_configuration import RunConfigurationSnapshot, load_run_configuration
from tests.factories.journal import admit_run, start_run, terminalize_run
from tests.factories.run_resources import run_resource_admission, run_resource_envelope


def _invocation(
    tmp_path: Path,
) -> tuple[RunConfigurationSnapshot, ResolvedCliState, ResolvedRunResourceScope]:
    configuration = tmp_path / "run.yml"
    configuration.write_text(
        "version: 1\nsource: {kind: file-plan, path: plan.yml}\n"
        "target: {repository_id: actual-subject}\n"
        "resources: {cpu_millis: 1000, memory_mib: 512, max_processes: 128}\n",
        encoding="utf-8",
    )
    snapshot = load_run_configuration(configuration)
    resources = snapshot.selection.resources
    assert resources is not None
    state = ResolvedCliState.capture(
        {
            "OH_GOON_DB": str(tmp_path / "state.db"),
            "OH_GOON_LEAGUE_FILE": str(tmp_path / "league.json"),
            SQLITE_JOURNAL_MODE_ENV: "DELETE",
        }
    )
    # Transport is passive: this fixture neither selects nor launches native tools.
    executable = str(Path(sys.executable).resolve())
    unit = "oh-goon-run-" + "a" * 32 + ".scope"
    scope = ResolvedRunResourceScope(
        "fleet-resource-transport",
        resources,
        unit,
        executable,
        "b" * 64,
        executable,
        "b" * 64,
        (),
        (
            executable,
            "--scope",
            f"--unit={unit}",
            "--expand-environment=no",
            "--property=CollectMode=inactive",
            "--",
        ),
    )
    return snapshot, state, scope


def _transferred_header(prepared: PreparedRunResourceHandoff) -> tuple[str, int]:
    header = json.loads(prepared.header)
    descriptor = os.dup(prepared.descriptor)
    header["descriptor"] = descriptor
    return json.dumps(header), descriptor


@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="Linux sealed FD unavailable")
def test_sealed_handoff_preserves_captured_selection_without_yaml_or_source_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, state, scope = _invocation(tmp_path)
    with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
        root = prepared.observation_path.parent
        assert root.stat().st_mode & 0o777 == 0o700
        assert not (root / "handoff.json").exists()
        header, descriptor = _transferred_header(prepared)
        packet = os.pread(descriptor, json.loads(header)["byte_count"], 0)
        assert prepared.sha256 == "sha256:" + hashlib.sha256(packet).hexdigest()
        snapshot.path.unlink()

        def forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("handoff must consume the original captured representation")

        monkeypatch.setattr("oh_goon.services.run_configuration.yaml.load", forbidden)
        restored = restore_run_resource_handoff(header)
        assert restored.snapshot == snapshot
        assert restored.state == state
        assert restored.scope == scope
        assert restored.sha256 == prepared.sha256
        assert restored.observation_path == prepared.observation_path
        with pytest.raises(OSError):
            os.fstat(descriptor)
    assert not root.exists()
    assert prepared.cleanup_errors == []


@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="Linux sealed FD unavailable")
def test_handoff_rejects_changed_digest_and_closes_transferred_descriptor(tmp_path: Path) -> None:
    snapshot, state, scope = _invocation(tmp_path)
    with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
        valid, _ = _transferred_header(prepared)
        assert restore_run_resource_handoff(valid).snapshot == snapshot
        header, descriptor = _transferred_header(prepared)
        changed = json.loads(header)
        changed["identity"] = "sha256:" + "0" * 64
        with pytest.raises(ExecutionAuthorityDivergence, match="bytes differ"):
            restore_run_resource_handoff(json.dumps(changed))
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_handoff_rejects_oversized_header_before_touching_descriptors() -> None:
    with pytest.raises(RunConfigurationInvalid, match="bound"):
        restore_run_resource_handoff(" " * 2049)


def test_resolved_state_preserves_caller_paths_and_explicit_sqlite_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, state, _ = _invocation(tmp_path)
    assert not state.database.exists()
    assert not state.league_preferences.exists()
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "other.db"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "other.json"))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, "AUTO")
    monkeypatch.setenv("OH_GOON_TRANSPORT_TEST", "preserved")
    state.restore()
    assert os.environ["OH_GOON_DB"] == str(state.database)
    assert os.environ["OH_GOON_LEAGUE_FILE"] == str(state.league_preferences)
    assert os.environ[SQLITE_JOURNAL_MODE_ENV] == "DELETE"
    assert os.environ["OH_GOON_TRANSPORT_TEST"] == "preserved"
    assert not state.database.exists()


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="Linux pidfd unavailable")
@pytest.mark.parametrize("terminate", (False, True), ids=("native-exit", "native-sigterm"))
def test_signal_forwarder_accepts_original_engine_process_identity(terminate: bool) -> None:
    forwarder = RunResourceSignalForwarder()

    def observe(event: CommandProcessLifecycleEvent) -> None:
        forwarder.observe(event)
        if event.kind == "PROCESS_STARTED" and terminate:
            forwarder.request(signal.SIGTERM, None)
            forwarder.pump()

    try:
        # Input is sent after the engine's real birth observer; the child stays alive
        # at that boundary without introducing a timing sleep or alternate launcher.
        result = run_command(
            sys.executable,
            "-c",
            "import sys; sys.stdin.buffer.read(1)",
            input_data=b"x",
            process_observer=observe,
            timeout=5,
            require_process_group_quiescence=True,
        )
        assert result.returncode == (-signal.SIGTERM if terminate else 0)
        assert forwarder.birth is not None
        assert forwarder.failure is None
    finally:
        forwarder.close()
    assert forwarder.descriptor is None


@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="Linux sealed FD unavailable")
@pytest.mark.parametrize("primary_failure", (False, True), ids=("completed", "primary-failure"))
def test_private_transport_cleanup_does_not_mask_original_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary_failure: bool
) -> None:
    snapshot, state, scope = _invocation(tmp_path)
    cleanup = tempfile.TemporaryDirectory.cleanup

    def failed_cleanup(directory: tempfile.TemporaryDirectory[str]) -> None:
        cleanup(directory)
        raise OSError("injected transport cleanup failure")

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", failed_cleanup)
    original = RuntimeError("original workload failure")
    if primary_failure:
        with pytest.raises(RuntimeError) as raised:
            with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
                raise original
        assert raised.value is original
        assert "HANDOFF_SCRATCH_CLEANUP_FAILED" in original.__notes__
    else:
        with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
            result = 7
        assert result == 7
    assert prepared.cleanup_errors == ["HANDOFF_SCRATCH_CLEANUP_FAILED"]


@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="Linux sealed FD unavailable")
@pytest.mark.parametrize("private_terminal", (True, False), ids=("publish", "private-parent-lost"))
def test_real_control_publication_and_ack_with_physical_scope_boundary_mocked(
    tmp_path: Path,
    private_terminal: bool,
) -> None:
    snapshot, state, scope = _invocation(tmp_path)
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "subject"})
    repository.path.mkdir()
    journal = Journal(LocalDatabase(state.database, journal_mode="DELETE"))
    child = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from oh_goon.cli_run_resources import verify_run_resource_handoff, publish_terminal_run_resource_context
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal

header, configuration, payload, private_terminal = sys.argv[1:]
expected = RunResourceEnvelopeEvidence.model_validate_json(payload)
# This replaces only the physical scope qualification. Sealing, file I/O,
# process ownership, ACK, Journal capture and text publication remain real.
with patch('oh_goon.cli_run_resources.verify_current_run_resource_scope', return_value=expected):
    captured, observed = verify_run_resource_handoff(header, Path(configuration))
assert observed == expected
for descriptor in (json.loads(header)['descriptor'], captured.acknowledgement_descriptor):
    try:
        os.fstat(descriptor)
    except OSError:
        pass
    else:
        raise AssertionError('child retained a consumed control descriptor')
runtime = SimpleNamespace(runs=Journal(LocalDatabase(captured.state.database, journal_mode='DELETE')))
if private_terminal == 'false':
    captured.terminal_path.parent.chmod(0o755)
    try:
        publish_terminal_run_resource_context(runtime, captured, expected)
    except FleetPlanInvalid:
        assert not captured.terminal_path.exists()
        print('private-parent-rejected')
    else:
        raise AssertionError('terminal evidence published to a nonprivate parent')
else:
    publish_terminal_run_resource_context(runtime, captured, expected)
    print('control-publication-complete')
"""
    with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
        expected = run_resource_envelope(scope.fleet_run_id).model_copy(
            update={
                "resources": scope.resources,
                "unit_name": scope.unit_name,
                "handoff_sha256": prepared.sha256,
                "memory_max_bytes": scope.resources.memory_bytes,
                "pids_max": scope.resources.max_processes,
            }
        )
        admit_run(
            journal,
            expected.fleet_run_id,
            run_resource_admission(repository, expected),
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
        start_run(journal, expected.fleet_run_id)
        terminalize_run(
            journal,
            expected.fleet_run_id,
            FleetRunTerminalIntent.FAIL,
            FleetRunFailure.recovery("Original fixture failure"),
        )
        acknowledged = False

        def acknowledge_started() -> None:
            nonlocal acknowledged
            if acknowledged or not prepared.observation_path.exists():
                return
            started = RunResourceEnvelopeEvidence.model_validate_json(
                resource_cli._read_control_json(prepared.observation_path)
            )
            assert started == expected
            assert prepared.observation_path.stat().st_mode & 0o777 == 0o600
            assert os.write(prepared.acknowledgement_write, b"1") == 1
            acknowledged = True

        completed = run_command(
            sys.executable,
            "-I",
            "-c",
            child,
            prepared.header,
            str(snapshot.path),
            expected.model_dump_json(),
            str(private_terminal).lower(),
            termination_requested=acknowledge_started,
            pass_fds=(prepared.descriptor, prepared.acknowledgement_read),
            timeout=10,
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=8192,
        )
        assert completed.returncode == 0, completed.stderr
        assert acknowledged
        assert completed.supervisor.final_process_group_quiescent
        assert completed.stdout.strip() == (
            "control-publication-complete" if private_terminal else "private-parent-rejected"
        )
        if private_terminal:
            context = RunResourceObservationContext.model_validate_json(
                resource_cli._read_control_json(prepared.terminal_path)
            )
            assert context.expected == expected
            assert context.terminal_status is FleetRunStatus.FAILED
            assert context.database.path == state.database
            assert prepared.terminal_path.stat().st_mode & 0o777 == 0o600
        else:
            assert not prepared.terminal_path.exists()
        # Child consumption must not close the parent's separate FD table.
        for descriptor in (prepared.descriptor, prepared.acknowledgement_read):
            os.fstat(descriptor)
        assert not list(prepared.observation_path.parent.glob("*.tmp"))
    assert prepared.cleanup_errors == []
    assert not prepared.observation_path.parent.exists()
