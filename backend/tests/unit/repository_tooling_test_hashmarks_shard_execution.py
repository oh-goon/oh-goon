from __future__ import annotations

import pytest

pytest.importorskip("hashmarks")

import json
from pathlib import Path

from hashmarks.verification_selection import (
    VerificationSelectionEnvelopeState,
    downstream_consumption_contract,
    verification_member,
    verification_membership,
    verification_selection_envelope,
)

from oh_goon.repository_tooling import hashmarks_shard_execution as subject
from oh_goon.repository_tooling.hashmarks_shard_execution import main
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_VERSION,
)

pytestmark = pytest.mark.dependency_complete

_REPOSITORY = "git-tree:cli"
_SOURCE = "sha256:" + "1" * 64


def _selection_files(tmp_path: Path, nodeid: str) -> tuple[Path, Path]:
    path, _, symbol = nodeid.partition("::")
    membership = verification_membership([verification_member(path, test_symbol=symbol or None)])
    envelope = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity=_REPOSITORY,
            source_identity=_SOURCE,
            codemap_generation=1,
            identity_generation=1,
            stale=False,
            membership=membership,
            owner_evidence={"selected": path},
            evidence_hashes={"verification": "sha256:" + "2" * 64},
        )
    )
    contract = downstream_consumption_contract(envelope)
    contract_path = tmp_path / "contract.json"
    envelope_path = tmp_path / "envelope.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return contract_path, envelope_path


def _argv(contract: Path, envelope: Path, repository: Path, state: Path, report: Path) -> list[str]:
    return [
        "oh-goon-hashmarks-shards",
        str(contract),
        str(envelope),
        "--repository",
        str(repository),
        "--admitted-repository-identity",
        _REPOSITORY,
        "--admitted-source-identity",
        _SOURCE,
        "--producer-artifact-identity",
        TEST_HASHMARKS_ARTIFACT_IDENTITY,
        "--batch-size",
        "1",
        "--state-root",
        str(state),
        "--report-path",
        str(report),
        "--json",
    ]


def test_hashmarks_cli_executes_native_flat_selection_then_resumes(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    contract, envelope = _selection_files(tmp_path, "tests/test_smoke.py::test_ok")
    state = tmp_path / "state"
    report_path = tmp_path / "reports" / "execution.json"
    argv = _argv(contract, envelope, tmp_path, state, report_path)

    monkeypatch.setattr("sys.argv", argv)
    assert main() == 0
    first = json.loads(capsys.readouterr().out)
    assert first["hashmarks_identity"] == f"hashmarks:{TEST_HASHMARKS_VERSION}"
    assert first["terminal_success"] is True
    assert first["missing"] == first["duplicates"] == first["extras"] == []
    assert json.loads(report_path.read_text(encoding="utf-8"))["terminal_success"] is True

    monkeypatch.setattr("sys.argv", argv)
    assert main() == 0
    second = json.loads(capsys.readouterr().out)
    assert second["terminal_success"] is True
    assert second["retry_bisection_lineage"][0]["resumed"] is True


def test_hashmarks_cli_rejects_repository_tamper_before_process_launch(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    contract, envelope = _selection_files(tmp_path, "tests/test_smoke.py::test_ok")
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("execution must not start")

    monkeypatch.setattr(
        "oh_goon.repository_tooling.hashmarks_shard_execution.execute_shard_plan", forbidden
    )
    argv = _argv(contract, envelope, tmp_path, tmp_path / "state", tmp_path / "receipt.json")
    argv[argv.index(_REPOSITORY)] = "git-tree:wrong"
    monkeypatch.setattr("sys.argv", argv)
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 2
    assert called is False
    assert "repository identity" in capsys.readouterr().err


def test_hashmarks_cli_requires_exact_artifact_and_source_identity(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    contract, envelope = _selection_files(tmp_path, "tests/test_smoke.py::test_ok")
    argv = [
        "oh-goon-hashmarks-shards",
        str(contract),
        str(envelope),
        "--admitted-repository-identity",
        _REPOSITORY,
    ]
    monkeypatch.setattr("sys.argv", argv)
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 2
    rendered = capsys.readouterr().err
    assert "admitted-source-identity" in rendered
    assert "producer-artifact-identity" in rendered


def test_cli_transports_execution_window_and_timeout_refinement_semantically(
    monkeypatch, tmp_path: Path
) -> None:
    selection = object()
    plan = object()
    report = object()
    receipt = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        subject, "load_hashmarks_verification_selection", lambda *_a, **_k: selection
    )
    monkeypatch.setattr(subject, "build_verification_execution_plan", lambda *_a, **_k: plan)

    def execute(*_args, **kwargs):
        captured.update(kwargs)
        return report

    monkeypatch.setattr(subject, "execute_shard_plan", execute)
    monkeypatch.setattr(subject, "build_verification_execution_receipt", lambda *_a, **_k: receipt)
    identity = "sha256:" + "1" * 64
    args = subject._parser().parse_args(
        [
            str(tmp_path / "contract.json"),
            str(tmp_path / "envelope.json"),
            "--admitted-repository-identity",
            _REPOSITORY,
            "--admitted-source-identity",
            _SOURCE,
            "--producer-artifact-identity",
            TEST_HASHMARKS_ARTIFACT_IDENTITY,
            "--runner-toolchain-identity",
            identity,
            "--dependency-identity",
            identity,
            "--environment-identity",
            identity,
            "--minimum-execution-window",
            "17",
            "--no-timeout-refinement",
        ]
    )
    observed_receipt, observed_report = subject._execute(args)
    assert observed_receipt is receipt
    assert observed_report is report
    assert captured["minimum_execution_window_seconds"] == 17
    assert captured["timeout_refinement"] is False
