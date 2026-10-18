from __future__ import annotations

import ast
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from oh_goon import cli_developer, workflow
from oh_goon.repository_tooling import certification_bootstrap_protocol as protocol
from oh_goon.services.certification_bootstrap import (
    CertificationBootstrapContext,
    CertificationEnvironmentError,
    CertificationSourceError,
)


ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = ROOT / "backend/src/oh_goon"
OWNER_PATH = SOURCE_ROOT / "repository_tooling/certification_bootstrap_protocol.py"


def _context(tmp_path: Path) -> CertificationBootstrapContext:
    state_root = tmp_path / "state"
    return CertificationBootstrapContext(
        repository_id="repo-m114",
        state_root=state_root,
        database=state_root / "oh-goon.db",
        league_file=state_root / "league.json",
        lock_root=state_root / "locks",
    )


def _producer_args(tmp_path: Path) -> Namespace:
    return Namespace(require_strict_executor=False, state_root=tmp_path / "state")


def _workflow_result(stdout: str, *, returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["oh-goon", "dev", "bootstrap-certification"],
        returncode,
        stdout=stdout,
    )


def _parse_workflow_success(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> tuple[str, dict[str, str], Path]:
    monkeypatch.setattr(
        workflow,
        "_backend_cli",
        lambda _command, *, env: _workflow_result(stdout, returncode=0),
    )
    return workflow._certification_environment()


def _parse_workflow_error(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> workflow.CertificationContextFailure:
    monkeypatch.setattr(
        workflow,
        "_backend_cli",
        lambda _command, *, env: _workflow_result(stdout, returncode=2),
    )
    with pytest.raises(workflow.CertificationContextFailure) as exc_info:
        workflow._certification_environment()
    return exc_info.value


def test_protocol_identifier_mutation_flows_producer_to_parser(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    success_id = "OH_GOON_CERTIFICATION_BOOTSTRAP_V3"
    error_id = "OH_GOON_CERTIFICATION_BOOTSTRAP_ERROR_V3"
    monkeypatch.setattr(protocol, "SUCCESS_RECORD_ID", success_id)
    monkeypatch.setattr(protocol, "ERROR_RECORD_ID", error_id)

    context = _context(tmp_path)
    monkeypatch.setattr(
        cli_developer,
        "prepare_certification_bootstrap",
        lambda **_kwargs: context,
    )
    assert cli_developer._bootstrap_certification_command(_producer_args(tmp_path)) == 0
    success_output = capsys.readouterr().out
    assert f"{success_id}=" in success_output
    assert "OH_GOON_CERTIFICATION_BOOTSTRAP=" not in success_output

    repo_id, env, lock_root = _parse_workflow_success(monkeypatch, success_output)
    assert repo_id == context.repository_id
    assert env["OH_GOON_DB"] == str(context.database)
    assert env["OH_GOON_LEAGUE_FILE"] == str(context.league_file)
    assert lock_root == context.lock_root

    def fail_environment(**_kwargs):
        raise CertificationEnvironmentError("m114 environment failure")

    monkeypatch.setattr(cli_developer, "prepare_certification_bootstrap", fail_environment)
    assert cli_developer._bootstrap_certification_command(_producer_args(tmp_path)) == 2
    error_output = capsys.readouterr().out
    assert f"{error_id}=" in error_output
    assert "OH_GOON_CERTIFICATION_BOOTSTRAP_ERROR=" not in error_output

    failure = _parse_workflow_error(monkeypatch, error_output)
    assert failure.classification == "environment"
    assert "m114 environment failure" in str(failure)


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected_message"),
    [
        ("no bootstrap record\n", 0, "invalid success response"),
        ("OH_GOON_CERTIFICATION_BOOTSTRAP={not-json}\n", 0, "invalid success response"),
        (
            protocol.render_success_record(
                {
                    "repository_id": "repo",
                    "database": "/tmp/db",
                    "league_file": "/tmp/league",
                    "lock_root": "/tmp/locks",
                }
            )
            + "\n"
            + protocol.render_success_record(
                {
                    "repository_id": "repo",
                    "database": "/tmp/db",
                    "league_file": "/tmp/league",
                    "lock_root": "/tmp/locks",
                }
            )
            + "\n",
            0,
            "invalid success response",
        ),
        ("no bootstrap error record\n", 2, "invalid error response"),
        ("OH_GOON_CERTIFICATION_BOOTSTRAP_ERROR={not-json}\n", 2, "invalid error response"),
        (
            protocol.render_error_record(
                {"classification": "application", "message": "first"}
            )
            + "\n"
            + protocol.render_error_record(
                {"classification": "application", "message": "second"}
            )
            + "\n",
            2,
            "invalid error response",
        ),
    ],
)
def test_workflow_rejects_missing_malformed_or_duplicate_protocol_records(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int,
    expected_message: str,
) -> None:
    monkeypatch.setattr(
        workflow,
        "_backend_cli",
        lambda _command, *, env: _workflow_result(stdout, returncode=returncode),
    )
    with pytest.raises(workflow.CertificationContextFailure, match=expected_message):
        workflow._certification_environment()


@pytest.mark.parametrize(
    ("error_type", "classification"),
    [
        (CertificationSourceError, "application"),
        (CertificationEnvironmentError, "environment"),
    ],
)
def test_application_and_environment_error_records_remain_distinct_and_handled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    error_type: type[RuntimeError],
    classification: str,
) -> None:
    def fail(**_kwargs):
        raise error_type(f"{classification} m114 failure")

    monkeypatch.setattr(cli_developer, "prepare_certification_bootstrap", fail)
    assert cli_developer._bootstrap_certification_command(_producer_args(tmp_path)) == 2
    output = capsys.readouterr().out

    payloads = protocol.error_payloads(output)
    assert len(payloads) == 1
    failure = _parse_workflow_error(monkeypatch, output)
    assert failure.classification == classification
    assert f"{classification} m114 failure" in str(failure)


def test_production_has_one_certification_bootstrap_record_identity_owner() -> None:
    identities = {protocol.SUCCESS_RECORD_ID, protocol.ERROR_RECORD_ID}
    literal_owners: dict[str, list[Path]] = {identity: [] for identity in identities}

    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in identities:
                literal_owners[str(node.value)].append(path)

    assert literal_owners == {
        protocol.SUCCESS_RECORD_ID: [OWNER_PATH],
        protocol.ERROR_RECORD_ID: [OWNER_PATH],
    }
    assert not hasattr(workflow, "CERTIFICATION_CONTEXT_PREFIX")
    assert not hasattr(workflow, "CERTIFICATION_CONTEXT_ERROR_PREFIX")
