"""M212: a prior APP success must never authorize a different enrolled Fleet Run."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon import workflow
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_observability import run_observability_command
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.services import plan_success_evidence


class _Target:
    def __init__(self):
        self.identity = SimpleNamespace(digest="same-plan")
        self.source = "same-plan-source"


class _Runs:
    def __init__(self, ids, admission):
        self.ids = ids
        self.admission = admission
        self.lookups = []

    def history(self, **kwargs):
        self.lookups.append(kwargs)
        return SimpleNamespace(
            items=tuple(
                SimpleNamespace(run=SimpleNamespace(target=_Target(), fleet_run_id=run_id))
                for run_id in self.ids
            ),
            next_before_revision=None,
        )

    def get(self, run_id):
        return SimpleNamespace(
            run=SimpleNamespace(fleet_run_id=run_id, target=_Target()),
            outcome=FleetRunOutcome.SUCCESS,
            repository_admissions=(self.admission,),
        )


def test_exact_run_binding_rejects_prior_success_and_accepts_only_current_run(
    tmp_path, monkeypatch
):
    repo_id = RepositoryIdentity(path=tmp_path).repository_id
    admission = object()
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _Target)
    monkeypatch.setattr(
        plan_success_evidence, "repository_plan_admission_evidence", lambda *_: admission
    )
    runs = _Runs(["previous-app"], admission)
    runtime = SimpleNamespace(
        plans=SimpleNamespace(
            prepare_execution_target=lambda *_a, **_kw: SimpleNamespace(
                identity=SimpleNamespace(digest="same-plan"),
                source="same-plan-source",
                plan=object(),
            )
        ),
        runs=runs,
    )
    kwargs = dict(
        plan=Path("app.yml"),
        repository_id=repo_id,
        source_root=tmp_path,
        lock_path=tmp_path / "app.goon.lock",
    )
    assert plan_success_evidence.has_exact_plan_success(runtime, **kwargs) is True
    assert (
        plan_success_evidence.has_exact_plan_success(
            runtime,
            **kwargs,
            fleet_run_id="enrolled-app",
        )
        is False
    )
    runs.ids = ["previous-app", "enrolled-app"]
    assert (
        plan_success_evidence.has_exact_plan_success(
            runtime,
            **kwargs,
            fleet_run_id="enrolled-app",
        )
        is True
    )
    for invalid in ("", "  ", 42):
        assert (
            plan_success_evidence.has_exact_plan_success(
                runtime,
                **kwargs,
                fleet_run_id=invalid,
            )
            is False
        )


def test_internal_verifier_forwards_exact_run_only_when_explicit(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "oh_goon.cli_runtime_observability.has_exact_plan_success",
        lambda *_a, **kwargs: seen.append(kwargs) or False,
    )
    parser = build_parser()
    base = [
        "dev",
        "require-plan-success",
        "--plan",
        "app.yml",
        "--repository-id",
        "repo",
        "--source-root",
        ".",
        "--lock-file",
        "app.goon.lock",
    ]
    assert run_observability_command(object(), parser.parse_args(base)) == 3
    assert "fleet_run_id" not in seen[-1]
    assert (
        run_observability_command(object(), parser.parse_args(base + ["--fleet-run-id", "run-a"]))
        == 3
    )
    assert seen[-1]["fleet_run_id"] == "run-a"


def test_deployment_callback_success_requires_exact_enrolled_run_receipt(monkeypatch, capsys):
    monkeypatch.setattr(workflow, "_prepare_plan_context", lambda **_kw: ("repo", {}, Path("/tmp")))
    monkeypatch.setattr(workflow, "_run_plan", lambda **_kw: (0, "Fleet Run ID: previous-app\n"))
    observed = []

    def verifier(*, fleet_run_id=None, **_kw):
        observed.append(fleet_run_id)
        return SimpleNamespace(returncode=3 if fleet_run_id == "enrolled-app" else 0)

    monkeypatch.setattr(workflow, "_require_current_app_pass", verifier)
    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=lambda *_a: None,
            deployment_fleet_run_id="enrolled-app",
        )
        == 2
    )
    assert observed == ["enrolled-app"]
    assert "APP CERTIFICATION PASSED" not in capsys.readouterr().out


def test_deployment_cannot_omit_explicit_exact_run_identity(monkeypatch):
    monkeypatch.setattr(
        workflow, "_prepare_plan_context", lambda **_kw: pytest.fail("bootstrap work")
    )
    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=lambda *_a: None,
        )
        == 2
    )


def test_bound_run_is_not_hidden_by_older_success_page(tmp_path, monkeypatch):
    repository_id = RepositoryIdentity(path=tmp_path).repository_id
    admission = object()
    monkeypatch.setattr(plan_success_evidence, "FleetPlanTarget", _Target)
    monkeypatch.setattr(
        plan_success_evidence, "repository_plan_admission_evidence", lambda *_: admission
    )
    old = SimpleNamespace(run=SimpleNamespace(target=_Target(), fleet_run_id="old"))
    current = SimpleNamespace(run=SimpleNamespace(target=_Target(), fleet_run_id="current"))
    pages = iter(
        [
            SimpleNamespace(items=(old,), next_before_revision=11),
            SimpleNamespace(items=(current,), next_before_revision=None),
        ]
    )
    queries = []

    def history(**kwargs):
        queries.append(kwargs)
        return next(pages)

    runtime = SimpleNamespace(
        plans=SimpleNamespace(
            prepare_execution_target=lambda *_a, **_kw: SimpleNamespace(
                identity=SimpleNamespace(digest="same-plan"),
                source="same-plan-source",
                plan=object(),
            )
        ),
        runs=SimpleNamespace(
            history=history,
            get=lambda run_id: SimpleNamespace(
                run=SimpleNamespace(fleet_run_id=run_id, target=_Target()),
                outcome=FleetRunOutcome.SUCCESS,
                repository_admissions=(admission,),
            ),
        ),
    )
    assert plan_success_evidence.has_exact_plan_success(
        runtime,
        plan=Path("app.yml"),
        repository_id=repository_id,
        source_root=tmp_path,
        lock_path=tmp_path / "app.goon.lock",
        fleet_run_id="current",
    )
    assert queries[0]["before_revision"] is None
    assert queries[1]["before_revision"] == 11


def test_deployment_app_success_verifier_passes_run_binding_to_backend_cli(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        workflow,
        "_backend_cli",
        lambda args, **_kwargs: (
            recorded.append(args),
            SimpleNamespace(returncode=3, stdout=""),
        )[1],
    )
    workflow._require_current_app_pass(
        repo_id="repo", env={}, lock_root=Path("/tmp"), fleet_run_id="run-x"
    )
    assert recorded[-1][-2:] == ["--fleet-run-id", "run-x"]
    recorded.clear()
    workflow._require_current_app_pass(repo_id="repo", env={}, lock_root=Path("/tmp"))
    assert "--fleet-run-id" not in recorded[-1]
