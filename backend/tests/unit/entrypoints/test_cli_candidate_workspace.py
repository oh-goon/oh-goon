"""Explicit candidate selection, private task transport and versioned cleanup CLI."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon import cli_ai
from oh_goon.cli_parser import build_parser
from oh_goon.core.models.agent_providers import AgentProviderError


def test_candidate_cli_selects_headless_once_and_preserves_logical_subject(
    tmp_path,
    monkeypatch,
    capsys,
):
    workspace = tmp_path / "developer"
    workspace.mkdir()
    selected = SimpleNamespace(interactive=False)
    resolve = Mock(return_value=(selected, object(), object()))
    execute = Mock(
        return_value=(
            0,
            SimpleNamespace(
                run=SimpleNamespace(
                    status=SimpleNamespace(value="COMPLETED"),
                    fleet_run_id="candidate-run",
                    target=None,
                ),
                outcome=SimpleNamespace(value="SUCCESS"),
                operations=(),
            ),
        )
    )
    validate = Mock()
    monkeypatch.setattr(cli_ai, "resolve_native_cli_selection", resolve)
    monkeypatch.setattr(cli_ai, "_run_and_close", execute)
    monkeypatch.setattr(
        "oh_goon.services.fleet_plan_candidate.require_native_candidate_selection", validate
    )
    monkeypatch.setattr(cli_ai.ApplicationRuntime, "build", Mock(return_value=object()))
    args = build_parser().parse_args(
        [
            "ai",
            "--agent",
            "pi",
            "--candidate",
            "--workspace",
            str(workspace),
            "--prompt",
            "Edit the candidate",
        ]
    )
    assert cli_ai.run_ai_command(args) == 0
    resolve.assert_called_once()
    assert resolve.call_args.kwargs["interactive"] is False
    assert resolve.call_args.kwargs["workspace"] == workspace
    validate.assert_called_once_with(selected)
    assert execute.call_args.kwargs["candidate"] is True
    assert execute.call_args.args[2] == workspace
    assert "runs candidate candidate-run --json" in capsys.readouterr().out


def test_interactive_candidate_refuses_before_configuration_or_runtime(monkeypatch, capsys):
    resolve, runtime = Mock(), Mock()
    monkeypatch.setattr(cli_ai, "resolve_native_cli_selection", resolve)
    monkeypatch.setattr(cli_ai.ApplicationRuntime, "build", runtime)
    args = build_parser().parse_args(
        [
            "ai",
            "--candidate",
            "--interactive",
            "--json",
            "--prompt",
            "Edit",
        ]
    )
    assert cli_ai.run_ai_command(args) == 2
    resolve.assert_not_called()
    runtime.assert_not_called()
    assert "CANDIDATE_PROFILE_UNAVAILABLE" in capsys.readouterr().out


def test_proposed_candidate_run_id_is_visible_before_possibly_applied_admission(tmp_path, capsys):
    observed = {}

    def refuse(**values):
        observed.update(values)
        visible = capsys.readouterr().err
        assert values["fleet_run_id"] in visible
        assert "runs" in visible and "handoff" in visible
        raise AgentProviderError("Preparation refused")

    runtime = SimpleNamespace(
        repositories=SimpleNamespace(exclusions=lambda: ()),
        locations=SimpleNamespace(list=lambda: (tmp_path,)),
        plans=SimpleNamespace(run_native_agent=refuse),
    )
    with pytest.raises(AgentProviderError):
        cli_ai._run_selected(runtime, object(), tmp_path, None, "Edit", (), candidate=True)
    assert observed["candidate"] is True
    assert observed["workspace"] == tmp_path


def test_retirement_requires_explicit_authorization_and_observed_version():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["runs", "retire-candidate", "run", "--expected-state-version", "5"])
    with pytest.raises(SystemExit):
        parser.parse_args(["runs", "retire-candidate", "run", "--authorize"])
    args = parser.parse_args(
        [
            "runs",
            "retire-candidate",
            "run",
            "--expected-state-version",
            "5",
            "--authorize",
            "--reconcile",
            "--json",
        ]
    )
    assert args.fleet_run_id == "run" and args.authorize and args.reconcile
    assert args.expected_state_version == 5
