from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import Mock

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_parser import build_parser
from oh_goon.cli_pi_session import run_pi_session_command
from oh_goon.cli_plan_run_readback import print_pi_session_readback
from oh_goon.core.errors import PiSessionRefusalCode
from oh_goon.core.models.game_tape_pi_session import GameTapePiSessionEvidence
from oh_goon.core.models.pi_session_storage import PiSessionInspection


def test_pi_retirement_requires_explicit_authorization() -> None:
    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["runs", "retire-pi-session", "fleet-original"])
    assert refused.value.code == 2


@pytest.mark.parametrize("retire", [False, True])
def test_pi_commands_select_one_original_run_only(
    capsys: pytest.CaptureFixture[str], retire: bool
) -> None:
    requested = [
        "runs",
        "retire-pi-session" if retire else "pi-session",
        "fleet-original",
        "--json",
    ]
    if retire:
        requested.append("--authorize")
    args = build_parser().parse_args(requested)
    evidence = PiSessionInspection(
        fleet_run_id="fleet-original",
        availability="unavailable" if retire else "available",
        retirement="verified" if retire else "none",
    )
    runs = Mock()
    runs.inspect_pi_session.return_value = evidence
    runs.retire_pi_session.return_value = evidence
    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=runs))

    assert run_pi_session_command(runtime, args) == 0
    if retire:
        runs.retire_pi_session.assert_called_once_with("fleet-original", authorize=True)
        runs.inspect_pi_session.assert_not_called()
    else:
        runs.inspect_pi_session.assert_called_once_with("fleet-original")
        runs.retire_pi_session.assert_not_called()
    assert json.loads(capsys.readouterr().out) == evidence.model_dump(mode="json")


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_pi_retirement_failure_keeps_storage_path_private(
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    from oh_goon.core.models.agent_providers import PiSessionCleanupUnverified

    argv = ["runs", "retire-pi-session", "fleet-original", "--authorize"]
    args = build_parser().parse_args([*argv, "--json"] if as_json else argv)
    runs = Mock()
    runs.retire_pi_session.side_effect = PiSessionCleanupUnverified(
        "Private /local-state/pi-sessions/selected/session.jsonl"
    )
    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=runs))

    assert run_pi_session_command(runtime, args) == 2
    captured = capsys.readouterr()
    reference = ["oh-goon", "runs", "pi-session", "fleet-original", "--json"]
    if as_json:
        report = json.loads(captured.out)
        assert report["code"] == PiSessionRefusalCode.CLEANUP_UNVERIFIED
        assert report["readback_argv"] == reference
    else:
        assert captured.out == ""
        assert PiSessionRefusalCode.CLEANUP_UNVERIFIED in captured.err
        assert json.dumps(reference) in captured.err
    assert "/local-state/pi-sessions" not in captured.out + captured.err


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
@pytest.mark.parametrize(
    ("availability", "code", "expected_action"),
    [
        ("busy", PiSessionRefusalCode.BUSY, "original writer to settle"),
        ("unavailable", PiSessionRefusalCode.UNAVAILABLE, "latest qualified writer"),
        ("unverified", PiSessionRefusalCode.CLEANUP_UNVERIFIED, "establish process cleanup"),
        (
            "orphaned",
            PiSessionRefusalCode.SETTLEMENT_UNCERTAIN,
            "Avoid replaying unresolved effects",
        ),
    ],
)
def test_pi_inspection_reports_unavailable_status_and_safe_guidance(
    capsys: pytest.CaptureFixture[str],
    availability: Literal["busy", "unavailable", "unverified", "orphaned"],
    code: PiSessionRefusalCode,
    expected_action: str,
    as_json: bool,
) -> None:
    argv = ["runs", "pi-session", "fleet-original"]
    args = build_parser().parse_args([*argv, "--json"] if as_json else argv)
    runs = Mock()
    runs.inspect_pi_session.return_value = PiSessionInspection(
        fleet_run_id="fleet-original", availability=availability, refusal_code=code
    )
    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=runs))

    assert run_pi_session_command(runtime, args) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        report = json.loads(captured.out)
        assert report["availability"] == availability
        assert report["refusal_code"] == code
        assert expected_action in report["safe_next_action"]
    else:
        assert f"Pi availability {availability}" in captured.out
        assert code in captured.out and expected_action in captured.out


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_pi_pending_retirement_guides_reconciliation_of_the_exact_run(
    capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    argv = ["runs", "pi-session", "fleet-original"]
    args = build_parser().parse_args([*argv, "--json"] if as_json else argv)
    runs = Mock()
    runs.inspect_pi_session.return_value = PiSessionInspection(
        fleet_run_id="fleet-original",
        availability="unavailable",
        retirement="pending",
        refusal_code=PiSessionRefusalCode.UNAVAILABLE,
    )
    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=runs))

    assert run_pi_session_command(runtime, args) == 0
    output = capsys.readouterr()
    action = json.loads(output.out)["safe_next_action"] if as_json else output.out
    assert "oh-goon runs retire-pi-session fleet-original --authorize --json" in action
    assert "original runtime" in action
    assert output.err == ""
    runs.retire_pi_session.assert_not_called()


@pytest.mark.parametrize("verified", [True, False])
def test_pi_history_text_shows_save_proof_only_when_recorded(
    capsys: pytest.CaptureFixture[str], verified: bool
) -> None:
    print_pi_session_readback(
        GameTapePiSessionEvidence(
            mode="new",
            repository_id="repository",
            step_id="agent",
            implementation_identity="pi:sha256:" + "a" * 64,
            handoff_identity="handoff",
            delivery_sha256="b" * 64,
            persistence="observed",
            byte_identity="digest_recorded",
            save_verification="pi_rpc_synced" if verified else None,
        )
    )
    output = capsys.readouterr().out
    assert "Recorded file evidence digest recorded" in output
    assert "Current Pi session availability was not checked" in output
    assert ("Recorded save verification pi_rpc_synced" in output) is verified
