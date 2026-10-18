from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon.cli_status import LAUNCH_STATUS_ERROR_SCHEMA, run_status_command
from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_status import LAUNCH_STATUS_SCHEMA
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_owner import BACKEND_GENERATION, BACKEND_SERVICE, LocalLaunchOwner, built_local_intent


def _args(*, as_json: bool) -> argparse.Namespace:
    return argparse.Namespace(command="status", as_json=as_json)


def _install_process_birth_observer(monkeypatch) -> None:
    observer = lambda pid: ProcessBirthIdentity(pid=pid, boot_id="boot-test", start_ticks=1)
    monkeypatch.setattr("oh_goon.runtime.local_launch_owner.observe_process_birth", observer)

    from oh_goon.core.launch_status import safe_launch_status as project_launch_status

    monkeypatch.setattr(
        "oh_goon.cli_status.safe_launch_status",
        lambda manifest: project_launch_status(manifest, process_observer=observer),
    )


def test_status_json_resolves_exact_cwd_binding_and_emits_one_safe_document(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _install_process_birth_observer(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, 321)

    assert run_status_command(_args(as_json=True)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema"] == LAUNCH_STATUS_SCHEMA
    assert payload["launch_id"] == owner.identity.launch_id
    assert payload["owner_proof"] == "available"
    assert set(payload) == {
        "schema",
        "launch_id",
        "owner_generation",
        "workspace",
        "profile",
        "lifecycle",
        "observation_freshness",
        "owner_proof",
        "services",
        "failure",
    }
    assert all(set(service) == {
        "service",
        "required",
        "service_generation",
        "startup",
        "readiness",
        "health",
        "bound_endpoint",
    } for service in payload["services"])
    # The persisted manifest contains the spawned PID 321 and launch commands, but
    # public status is a semantic whitelist. Never search the serialized JSON for a
    # short value: an unrelated hash/path may legitimately contain those characters.
    assert "pid" not in payload
    assert "command" not in payload
    assert all("pid" not in service and "command" not in service for service in payload["services"])


def test_status_json_allows_safe_fields_to_contain_pid_like_text(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _install_process_birth_observer(monkeypatch)
    # Regression: safety is defined by the JSON schema/fields, not substring absence.
    # A workspace may legitimately contain the same digits as a private process PID.
    workspace = tmp_path / "repo-321-command"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, 321)

    assert run_status_command(_args(as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "321-command" in payload["workspace"]
    assert "pid" not in payload
    assert "command" not in payload
    assert all("pid" not in service and "command" not in service for service in payload["services"])


def test_status_human_preserves_authoritative_lifecycle_during_owner_uncertainty(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _install_process_birth_observer(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, 321)
    owner.store.append(
        owner.identity.launch_id,
        event=LaunchEventKind.SERVICE_STARTUP_COMPLETE,
        asserted_by="backend-runtime",
        timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
        service=BACKEND_SERVICE,
        service_generation=BACKEND_GENERATION,
    )
    owner.bound(BACKEND_SERVICE, BACKEND_GENERATION, host="127.0.0.1", port=8787)
    owner.ready(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.launch_ready()

    assert run_status_command(_args(as_json=False)) == 0
    output = capsys.readouterr().out
    assert "Lifecycle:     ready" in output
    assert "Freshness:     current" in output
    assert "Owner proof:   available" in output


def test_status_preserves_ready_when_recorded_owner_birth_cannot_be_observed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
        owner_pid=99999999,
    )
    # No birth proof can be recorded for the nonexistent owner. Lifecycle remains
    # authoritative; status reports proof uncertainty rather than inventing terminal.
    assert run_status_command(_args(as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "resolving"
    assert payload["observation_freshness"] == "stale"
    assert payload["owner_proof"] == "unavailable"


def test_status_without_binding_does_not_scan_launch_records(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    assert run_status_command(_args(as_json=False)) == 1
    output = capsys.readouterr().out
    assert "LAUNCH STATUS UNAVAILABLE" in output
    assert "no current local launch binding" in output


def test_status_json_failure_uses_distinct_error_envelope_schema(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "missing-repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))

    assert run_status_command(_args(as_json=True)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "launch_status_unavailable",
        "schema": LAUNCH_STATUS_ERROR_SCHEMA,
    }
    assert payload["schema"] != LAUNCH_STATUS_SCHEMA
