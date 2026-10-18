"""M219: deployment APP must never mix different checkout/process authorities."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon import workflow
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.sqlite_journal_vocabulary import (
    SQLITE_JOURNAL_DELETE,
    SQLITE_JOURNAL_MODE_ENV,
)
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner


def _owner(monkeypatch):
    owner = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(
        owner, "executor_inventory_file", Path("/deployment/inventory.toml")
    )
    monkeypatch.setattr(
        FleetPreworkProvisioner, "require_enrolled_identity", lambda *_: None
    )
    monkeypatch.setattr(
        FleetPreworkProvisioner,
        "inspect_deployment_readiness",
        lambda *_: {"status": "OBSERVED_NOT_ADMITTED"},
    )
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    return owner


def test_changed_cwd_denied_before_source_admission_and_state(tmp_path, monkeypatch):
    owner = _owner(monkeypatch)
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        frontdoor,
        "_static_source_admission",
        lambda: pytest.fail("static admission started"),
    )
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda *_a, **_kw: pytest.fail("state started"),
    )
    with pytest.raises(TrustProtocolError, match="source root"):
        frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a")


def test_source_context_flip_after_inspection_denies_before_bootstrap(
    tmp_path, monkeypatch
):
    owner = _owner(monkeypatch)
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    env = {
        "OH_GOON_DB": "db",
        "OH_GOON_LEAGUE_FILE": "league",
        SQLITE_JOURNAL_MODE_ENV: SQLITE_JOURNAL_DELETE,
    }
    monkeypatch.setenv("OH_GOON_DB", "db")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", "league")
    assert (
        frontdoor._deployment_context_mismatch(
            env, fleet_provisioner=owner, fleet_run_id="run-a"
        )
        is None
    )
    monkeypatch.chdir(tmp_path)
    assert "source root" in (
        frontdoor._deployment_context_mismatch(
            env, fleet_provisioner=owner, fleet_run_id="run-a"
        )
        or ""
    )
    monkeypatch.setattr(
        workflow, "_backend_cli", lambda *_a, **_kw: pytest.fail("bootstrap started")
    )
    rc, reason = workflow._run_plan(
        plan=workflow.APP_PLAN,
        repo_id="repo",
        env=env,
        lock_root=tmp_path,
        deployment_context_guard=lambda actual: frontdoor._deployment_context_mismatch(
            actual, fleet_provisioner=owner, fleet_run_id="run-a"
        ),
        deployment_plan_run=lambda *_: pytest.fail("Plan started"),
    )
    assert rc == 2 and "source root" in reason


def test_late_context_drift_cannot_accept_successful_callback_as_app_pass(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    env = {"OH_GOON_DB": "db", "OH_GOON_LEAGUE_FILE": "league"}
    monkeypatch.setattr(
        workflow, "_prepare_plan_context", lambda **_kw: ("repo-a", env, tmp_path)
    )

    def plan_then_drift(**_kwargs):
        monkeypatch.chdir(tmp_path)
        return 0, ""

    monkeypatch.setattr(workflow, "_run_plan", plan_then_drift)
    monkeypatch.setattr(
        workflow,
        "_require_current_app_pass",
        lambda **_kw: pytest.fail("evidence accepted"),
    )

    def guard(_env):
        return (
            "Deployment source root differs from imported workflow authority"
            if Path.cwd().resolve() != workflow.ROOT
            else None
        )

    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=guard,
            deployment_fleet_run_id="run-a",
        )
        == 2
    )


def test_unchanged_context_still_checks_exact_run_persisted_success(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    env = {"OH_GOON_DB": "db", "OH_GOON_LEAGUE_FILE": "league"}
    monkeypatch.setattr(
        workflow, "_prepare_plan_context", lambda **_kw: ("repo-a", env, tmp_path)
    )
    monkeypatch.setattr(workflow, "_run_plan", lambda **_kw: (0, ""))
    checks = []
    monkeypatch.setattr(
        workflow,
        "_require_current_app_pass",
        lambda **kw: (checks.append(kw), SimpleNamespace(returncode=0))[1],
    )
    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=lambda _env: None,
            deployment_fleet_run_id="run-a",
        )
        == 0
    )
    assert len(checks) == 1 and checks[0]["fleet_run_id"] == "run-a"


def test_changed_cwd_denied_for_read_only_deployment_readiness(tmp_path, monkeypatch):
    owner = _owner(monkeypatch)
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TrustProtocolError, match="source root"):
        frontdoor.inspect_deployment_certification_readiness(
            fleet_provisioner=owner, fleet_run_id="run-a"
        )


def test_post_plan_control_state_drift_blocks_evidence_read(tmp_path, monkeypatch):
    owner = _owner(monkeypatch)
    monkeypatch.setattr(workflow, "ROOT", Path.cwd().resolve())
    monkeypatch.setenv("OH_GOON_DB", "db")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", "league")
    env = {
        "OH_GOON_DB": "db",
        "OH_GOON_LEAGUE_FILE": "league",
        SQLITE_JOURNAL_MODE_ENV: SQLITE_JOURNAL_DELETE,
    }
    monkeypatch.setattr(
        workflow, "_prepare_plan_context", lambda **_kw: ("repo-a", env, tmp_path)
    )

    def callback_changed_control(**_kwargs):
        monkeypatch.setenv("OH_GOON_DB", "different-db")
        return 0, ""

    monkeypatch.setattr(workflow, "_run_plan", callback_changed_control)
    monkeypatch.setattr(
        workflow,
        "_require_current_app_pass",
        lambda **_kw: pytest.fail("evidence queried"),
    )
    assert (
        workflow._run_self_plan(
            "certify",
            deployment_plan_run=lambda *_a: (0, ""),
            deployment_context_guard=lambda actual: frontdoor._deployment_context_mismatch(
                actual, fleet_provisioner=owner, fleet_run_id="run-a"
            ),
            deployment_fleet_run_id="run-a",
        )
        == 2
    )
