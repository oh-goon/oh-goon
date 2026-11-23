from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_parser import build_parser
from oh_goon.cli_pi_session import run_pi_session_command
from oh_goon.core.errors import PiSessionRefusalCode
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


def test_pi_retirement_failure_keeps_storage_path_private(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from oh_goon.core.models.agent_providers import PiSessionCleanupUnverified

    args = build_parser().parse_args(
        ["runs", "retire-pi-session", "fleet-original", "--authorize", "--json"]
    )
    runs = Mock()
    runs.retire_pi_session.side_effect = PiSessionCleanupUnverified(
        "Private /local-state/pi-sessions/selected/session.jsonl"
    )
    runtime = cast(ApplicationRuntime, SimpleNamespace(runs=runs))

    assert run_pi_session_command(runtime, args) == 2
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["code"] == PiSessionRefusalCode.CLEANUP_UNVERIFIED
    assert report["readback_argv"] == ["oh-goon", "runs", "pi-session", "fleet-original", "--json"]
    assert "/local-state/pi-sessions" not in output
