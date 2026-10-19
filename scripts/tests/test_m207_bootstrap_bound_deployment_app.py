"""M207 fail-before-work APP argument and original bootstrap identity regression."""

from __future__ import annotations

from typing import Any

from collections.abc import Callable

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon import workflow


def test_deployment_app_rejects_extra_inputs_before_league_and_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow, "_backend_cli", lambda *_a, **_kw: pytest.fail("bootstrap mutated")
    )
    seen = []

    def record_deployment_context_guard(_env: Any) -> None:
        seen.append("guard")
        return None

    rc, why = workflow._run_plan(
        plan=workflow.APP_PLAN,
        repo_id="repo-a",
        env={},
        lock_root=Path("/state/locks"),
        inputs={"alternate_repo": "repo-b"},
        deployment_context_guard=record_deployment_context_guard,
        deployment_plan_run=lambda *_a: pytest.fail("APP workload called"),
    )
    assert rc == 2 and "without inputs" in why
    assert seen == []


def test_deployment_callback_binds_exact_bootstrap_repo_and_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow,
        "_prepare_plan_context",
        lambda **_kw: (
            "repo-a",
            {},
            Path("/state/locks"),
        ),
    )
    actual = []

    def malicious_plan(
        *,
        deployment_plan_run: Callable[[list[str], dict[str, str]], tuple[int, str]]
        | None,
        **_kwargs: object,
    ) -> tuple[int, str]:
        assert deployment_plan_run is not None
        original = [
            "plan",
            "run",
            workflow.APP_PLAN,
            "--repository-id",
            "repo-a",
            "--lock-file",
            "/state/locks/app-certification.goon.lock",
            "--wait",
            "--repair-handoff",
        ]
        for changed in (
            [*original[:4], "repo-b", *original[5:]],
            [*original[:6], "/attacker/app-certification.goon.lock", *original[7:]],
            [*original, "--fleet-run-id", "run-forged"],
        ):
            rc, why = deployment_plan_run(changed, {})
            assert rc == 2 and "bootstrap" in why
        return deployment_plan_run(original, {})

    monkeypatch.setattr(workflow, "_run_plan", malicious_plan)
    monkeypatch.setattr(
        workflow,
        "_require_current_app_pass",
        lambda **_kw: SimpleNamespace(returncode=0),
    )

    def record_deployment_plan_run(args: Any, env: Any) -> tuple[int, str]:
        actual.append((args, env))
        return (0, "")

    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=record_deployment_plan_run,
            deployment_context_guard=lambda _env: None,
            deployment_fleet_run_id="run-a",
        )
        == 0
    )
    assert len(actual) == 1 and actual[0][0][4] == "repo-a"


def test_false_callback_success_cannot_bypass_persisted_app_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        workflow,
        "_prepare_plan_context",
        lambda **_kw: (
            "repo-a",
            {},
            Path("/state/locks"),
        ),
    )
    monkeypatch.setattr(
        workflow, "_run_plan", lambda **_kw: (0, "Fleet Run ID: old-run\n")
    )
    monkeypatch.setattr(
        workflow,
        "_require_current_app_pass",
        lambda **_kw: SimpleNamespace(returncode=2),
    )
    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=lambda _env: None,
            deployment_fleet_run_id="run-a",
        )
        == 2
    )
    assert "APP CERTIFICATION PASSED" not in capsys.readouterr().out


def test_non_deployment_plan_with_inputs_preserves_existing_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []

    def record_backend_cli(args: Any, **_kw: Any) -> SimpleNamespace:
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(
        workflow,
        "_backend_cli",
        record_backend_cli,
    )
    monkeypatch.setattr(workflow, "_stream", lambda *_a, **_kw: (0, ""))
    rc, _ = workflow._run_plan(
        plan=workflow.APP_PLAN,
        repo_id="repo-a",
        env={},
        lock_root=Path("/state/locks"),
        inputs={"test": "input"},
    )
    assert rc == 0
    assert "--input" in commands[1]
