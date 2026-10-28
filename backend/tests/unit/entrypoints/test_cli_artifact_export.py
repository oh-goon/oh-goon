"""Public artifact export arguments delegate effects and verification to the Run owner."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.fleet_run_control_plane import FleetArtifactExport


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("repository", [None, "repo-selected"])
def test_export_cli_transports_exact_selection_and_reports_verified_result(
    tmp_path, capsys, as_json, repository
):
    destination = tmp_path / "model with spaces.json"
    exported = FleetArtifactExport(
        fleet_run_id="run-a",
        repository_id=repository or "repo-recorded",
        step_id="iris.train",
        artifact_path="ml-output/model.json",
        destination=destination,
        sha256="a" * 64,
        byte_count=123,
        reference_id="ref-" + "b" * 32,
    )
    export = Mock(return_value=exported)
    runtime = cast(
        ApplicationRuntime, SimpleNamespace(runs=SimpleNamespace(export_artifact=export))
    )
    argv = [
        "plan",
        "export-artifact",
        "run-a",
        "--step",
        "iris.train",
        "--path",
        "ml-output/model.json",
        "--output",
        str(destination),
    ]
    if as_json:
        argv.append("--json")
    if repository is not None:
        argv.extend(("--repository-id", repository))
    assert run_plan_command(runtime, build_parser().parse_args(argv)) == 0
    export.assert_called_once_with(
        "run-a",
        step_id="iris.train",
        artifact_path="ml-output/model.json",
        destination=destination,
        repository_id=repository,
    )
    output = capsys.readouterr().out
    if as_json:
        result = json.loads(output)
        assert result["schema"] == "oh-goon.plan-artifact-export/v1"
        assert result["status"] == "exported"
        assert result["destination"] == str(destination)
        assert result["repository_id"] == exported.repository_id
        assert result["sha256"] == exported.sha256
        assert result["byte_count"] == 123
    else:
        assert f"Exported: {destination}" in output
        assert "Verified: 123 bytes; sha256:" + "a" * 64 in output


@pytest.mark.parametrize(
    "exception", [FleetPlanInvalid("Artifact was not retained"), OSError("Storage is full")]
)
def test_export_cli_refusal_has_nonzero_exit_and_no_success_receipt(capsys, exception):
    export = Mock(side_effect=exception)
    runtime = cast(
        ApplicationRuntime, SimpleNamespace(runs=SimpleNamespace(export_artifact=export))
    )
    args = build_parser().parse_args(
        [
            "plan",
            "export-artifact",
            "run-a",
            "--step",
            "train",
            "--path",
            "model.json",
            "--output",
            "model.json",
            "--json",
        ]
    )
    assert run_plan_command(runtime, args) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "error"
    assert receipt["message"] == str(exception)
    assert "sha256" not in receipt
    assert export.call_count == 1


@pytest.mark.parametrize("missing", ["--step", "--path", "--output"])
def test_export_cli_requires_complete_explicit_selection(missing):
    argv = [
        "plan",
        "export-artifact",
        "run-a",
        "--step",
        "train",
        "--path",
        "model.json",
        "--output",
        "model.json",
    ]
    index = argv.index(missing)
    del argv[index : index + 2]
    with pytest.raises(SystemExit) as rejected:
        build_parser().parse_args(argv)
    assert rejected.value.code == 2


@pytest.mark.parametrize("as_json", [False, True])
def test_export_cli_reports_published_destination_separately_from_failed_cleanup(
    tmp_path, capsys, as_json
):
    destination = tmp_path / "model.json"
    destination.write_bytes(b"exported model")
    exported = FleetArtifactExport(
        fleet_run_id="run-a",
        repository_id="repo-recorded",
        step_id="train",
        artifact_path="model.json",
        destination=destination,
        sha256="a" * 64,
        byte_count=14,
        reference_id="ref-" + "b" * 32,
        cleanup_status="failed",
        cleanup_failure="OSError: private staging cleanup failed",
    )
    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(export_artifact=Mock(return_value=exported))),
    )
    argv = [
        "plan",
        "export-artifact",
        "run-a",
        "--step",
        "train",
        "--path",
        "model.json",
        "--output",
        str(destination),
    ]
    if as_json:
        argv.append("--json")
    assert run_plan_command(runtime, build_parser().parse_args(argv)) == 0
    output = capsys.readouterr().out
    if as_json:
        receipt = json.loads(output)
        assert receipt["status"] == "exported"
        assert receipt["destination"] == str(destination)
        assert receipt["cleanup_status"] == "failed"
        assert receipt["cleanup_failure"] == exported.cleanup_failure
    else:
        assert f"Exported: {destination}" in output
        assert "Export completed; private staging cleanup failed" in output


@pytest.mark.parametrize("as_json", [False, True])
def test_export_cli_preserves_primary_error_and_displays_cleanup_notes(capsys, as_json):
    primary = FleetPlanInvalid("Original retained artifact mismatch")
    primary.add_note("Artifact export staging cleanup failed: synthetic storage error")
    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(export_artifact=Mock(side_effect=primary))),
    )
    argv = [
        "plan",
        "export-artifact",
        "run-a",
        "--step",
        "train",
        "--path",
        "model.json",
        "--output",
        "model.json",
    ]
    if as_json:
        argv.append("--json")
    assert run_plan_command(runtime, build_parser().parse_args(argv)) == 2
    output = capsys.readouterr().out
    if as_json:
        receipt = json.loads(output)
        assert receipt["message"] == str(primary)
        assert receipt["notes"] == primary.__notes__
        assert receipt["status"] == "error"
    else:
        assert str(primary) in output
        assert primary.__notes__[0] in output
