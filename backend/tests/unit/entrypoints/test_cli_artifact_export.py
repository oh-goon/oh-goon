"""Public artifact export arguments delegate effects and verification to the Run owner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.exact_file_write import ExactPublicationDurabilityError
from oh_goon.services.fleet_run_control_plane import FleetArtifactExport


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("repository", [None, "repo-selected"])
def test_export_cli_transports_exact_selection_and_reports_verified_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
    repository: str | None,
) -> None:
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
        "runs",
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


@pytest.mark.parametrize("exception_type", [FleetPlanInvalid, OSError])
def test_export_cli_refusal_has_nonzero_exit_without_private_error_or_success_receipt(
    capsys: pytest.CaptureFixture[str], exception_type: type[Exception]
) -> None:
    canary = "PRIVATE_EXPORT_FAILURE_CANARY"
    exception = exception_type(canary)
    exception.add_note(canary)
    export = Mock(side_effect=exception)
    runtime = cast(
        ApplicationRuntime, SimpleNamespace(runs=SimpleNamespace(export_artifact=export))
    )
    args = build_parser().parse_args(
        [
            "runs",
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
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt["status"] == "error"
    assert receipt["code"] == "ARTIFACT_EXPORT_FAILED"
    assert receipt["diagnostic_note_count"] == 1
    assert receipt["diagnostic_notes"] == "withheld_private_exception_text"
    assert receipt["publication"] == "NOT_CONFIRMED"
    assert canary not in captured.out + captured.err
    assert "sha256" not in receipt
    assert export.call_count == 1


@pytest.mark.parametrize("missing", ["--step", "--path", "--output"])
def test_export_cli_requires_complete_explicit_selection(missing: str) -> None:
    argv = [
        "runs",
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
    tmp_path: Path, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
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
        cleanup_failure="OSError: PRIVATE_SUCCESS_CLEANUP_CANARY; private staging: /private/path",
    )
    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(export_artifact=Mock(return_value=exported))),
    )
    argv = [
        "runs",
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
    captured = capsys.readouterr()
    output = captured.out
    assert "PRIVATE_SUCCESS_CLEANUP_CANARY" not in captured.out + captured.err
    assert "/private/path" not in captured.out + captured.err
    if as_json:
        receipt = json.loads(output)
        assert receipt["status"] == "exported"
        assert receipt["destination"] == str(destination)
        assert receipt["cleanup_status"] == "failed"
        assert receipt["cleanup_failure"] == "STAGING_CLEANUP_FAILED"
        assert receipt["sha256"] == exported.sha256 and receipt["byte_count"] == 14
    else:
        assert f"Exported: {destination}" in output
        assert "Export completed; private staging cleanup failed" in output
    assert destination.read_bytes() == b"exported model"


@pytest.mark.parametrize("as_json", [False, True])
def test_export_cli_preserves_primary_non_success_and_cleanup_note_count_without_private_text(
    capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    primary = FleetPlanInvalid("PRIVATE_PRIMARY_EXPORT_CANARY")
    primary.add_note("Artifact export staging cleanup failed: PRIVATE_CLEANUP_NOTE_CANARY")
    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(export_artifact=Mock(side_effect=primary))),
    )
    argv = [
        "runs",
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
    captured = capsys.readouterr()
    output = captured.out
    assert "PRIVATE_PRIMARY_EXPORT_CANARY" not in output + captured.err
    assert "PRIVATE_CLEANUP_NOTE_CANARY" not in output + captured.err
    if as_json:
        receipt = json.loads(output)
        assert receipt["code"] == "ARTIFACT_EXPORT_FAILED"
        assert receipt["diagnostic_note_count"] == 1
        assert receipt["diagnostic_notes"] == "withheld_private_exception_text"
        assert receipt["publication"] == "NOT_CONFIRMED"
        assert receipt["status"] == "error"
    else:
        assert "Artifact export failed" in output
        assert "publication/cleanup diagnostic notes are present" in output
    assert str(primary) == "PRIVATE_PRIMARY_EXPORT_CANARY"
    assert primary.__notes__ == [
        "Artifact export staging cleanup failed: PRIVATE_CLEANUP_NOTE_CANARY"
    ]


@pytest.mark.parametrize("as_json", [False, True])
def test_export_cli_preserves_typed_visible_publication_without_private_durability_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    destination = tmp_path / "visible-model.json"
    destination.write_bytes(b"published model")
    canary = "PRIVATE_EXPORT_DURABILITY_CANARY"
    failure = ExactPublicationDurabilityError(canary)
    failure.add_note("Published destination has unverified durability: " + canary)
    export = Mock(side_effect=failure)
    runtime = cast(
        ApplicationRuntime, SimpleNamespace(runs=SimpleNamespace(export_artifact=export))
    )
    arguments = [
        "runs",
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
        arguments.append("--json")

    assert run_plan_command(runtime, build_parser().parse_args(arguments)) == 2

    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
    assert destination.read_bytes() == b"published model"
    export.assert_called_once()
    if as_json:
        receipt = json.loads(captured.out)
        assert receipt["status"] == "error" and receipt["code"] == "ARTIFACT_EXPORT_FAILED"
        assert receipt["publication"] == "VISIBLE_DURABILITY_UNVERIFIED"
        assert receipt["diagnostic_note_count"] == 1
        assert receipt["diagnostic_notes"] == "withheld_private_exception_text"
        assert "sha256" not in receipt
    else:
        assert "VISIBLE_DURABILITY_UNVERIFIED" in captured.out
        assert "sha256:" not in captured.out


@pytest.mark.parametrize("published", [False, True])
def test_main_export_json_preserves_publication_and_exit_without_private_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    published: bool,
) -> None:
    canary = "PRIVATE_MAIN_EXPORT_CANARY"
    destination = tmp_path / "promoted-model.json"
    primary = OSError(canary)
    primary.add_note("Artifact export staging cleanup failed: " + canary)
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
        cleanup_failure=canary,
    )
    if published:
        destination.write_bytes(b"exported model")
    export = Mock(return_value=exported) if published else Mock(side_effect=primary)
    shutdown = Mock()
    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(export_artifact=export), shutdown=shutdown),
    )
    build = Mock(return_value=runtime)
    monkeypatch.setattr(ApplicationRuntime, "build", build)
    assert main(
        [
            "runs",
            "export-artifact",
            "run-a",
            "--step",
            "train",
            "--path",
            "model.json",
            "--output",
            str(destination),
            "--json",
        ]
    ) == (0 if published else 2)

    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert canary not in captured.out + captured.err
    assert receipt["_cli"]["exit_code"] == (0 if published else 2)
    assert build.call_args.kwargs["startup_purpose"] == "observation"
    shutdown.assert_called_once_with(wait_for_runs=False)
    export.assert_called_once()
    if published:
        assert receipt["status"] == "exported"
        assert receipt["sha256"] == exported.sha256 and receipt["byte_count"] == 14
        assert receipt["cleanup_status"] == "failed"
        assert receipt["cleanup_failure"] == "STAGING_CLEANUP_FAILED"
        assert receipt["destination"] == str(destination)
        assert destination.read_bytes() == b"exported model"
    else:
        assert receipt["status"] == "error" and receipt["code"] == "ARTIFACT_EXPORT_FAILED"
        assert receipt["diagnostic_note_count"] == 1
        assert receipt["publication"] == "NOT_CONFIRMED"
        assert "sha256" not in receipt and not destination.exists()
