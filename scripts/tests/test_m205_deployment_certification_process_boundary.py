"""M205 original-owner in-process APP boundary regressions."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon import cli, workflow
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_DELETE, SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner


def _original_owner(monkeypatch) -> FleetPreworkProvisioner:
    owner = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(owner, "executor_inventory_file", Path("/deployment/inventory.toml"))
    monkeypatch.setattr(FleetPreworkProvisioner, "require_enrolled_identity", lambda self, rid: None)
    monkeypatch.setattr(FleetPreworkProvisioner, "inspect_deployment_readiness", lambda self, rid: {"status": "OBSERVED_NOT_ADMITTED"})
    return owner



def _canonical_control_state(tmp_path, monkeypatch) -> tuple[str, str]:
    """A1 pre-lease authority: preselect exact DB/League before observing signer."""
    state = frontdoor.certification_state_root(
        source_root=Path.cwd(), state_home=tmp_path,
    )
    db, league = str(state / "oh-goon.db"), str(state / "league.yml")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("OH_GOON_DB", db)
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", league)
    return db, league

def test_wrong_object_is_rejected_before_static_source_or_state(monkeypatch) -> None:
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: pytest.fail("source read"))
    with pytest.raises(TrustProtocolError, match="original deployment"):
        frontdoor.certify_deployment_run(fleet_provisioner=object(), fleet_run_id="run-a")


def test_unenrolled_run_fails_before_static_source_and_state(monkeypatch) -> None:
    owner = _original_owner(monkeypatch)
    def deny(_self, _run_id):
        raise TrustProtocolError("Fleet Run is not explicitly provisioned")
    monkeypatch.setattr(FleetPreworkProvisioner, "require_enrolled_identity", deny)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: pytest.fail("source read"))
    with pytest.raises(TrustProtocolError, match="not explicitly provisioned"):
        frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="other")


def test_missing_original_inventory_and_journal_fail_before_static_source(monkeypatch) -> None:
    owner = _original_owner(monkeypatch)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: pytest.fail("source read"))
    monkeypatch.delenv(EXECUTORS_FILE_ENV, raising=False)
    with pytest.raises(TrustProtocolError, match="inventory"):
        frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a")
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.delenv(SQLITE_JOURNAL_MODE_ENV, raising=False)
    with pytest.raises(TrustProtocolError, match="journal mode"):
        frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a")


def test_source_admission_denial_does_not_touch_owner_observation_or_lease(monkeypatch) -> None:
    owner = _original_owner(monkeypatch)
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: False)
    monkeypatch.setattr(frontdoor, "_workflow_main_with_state_lease", lambda *_a, **_kw: pytest.fail("lease"))
    assert frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a") == 2


def test_original_object_reaches_inprocess_owner_callback_without_serialization(tmp_path, monkeypatch) -> None:
    owner = _original_owner(monkeypatch)
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    db, league = _canonical_control_state(tmp_path, monkeypatch)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    seen = []
    def run_app(*, deployment_plan_run, deployment_context_guard, fleet_run_id):
        assert fleet_run_id == "run-a"
        seen.append(deployment_context_guard({"OH_GOON_DB": "db", "OH_GOON_LEAGUE_FILE": "league", SQLITE_JOURNAL_MODE_ENV: SQLITE_JOURNAL_DELETE}))
        seen.append(deployment_context_guard({"OH_GOON_DB": db, "OH_GOON_LEAGUE_FILE": league, SQLITE_JOURNAL_MODE_ENV: SQLITE_JOURNAL_DELETE}))
        return deployment_plan_run(["plan", "run", workflow.APP_PLAN, "--repository-id", "repo-a", "--lock-file", "/state/app-certification.goon.lock", "--wait", "--repair-handoff"], dict(os.environ))[0]
    monkeypatch.setattr(workflow, "run_app_for_deployment", run_app)
    monkeypatch.setattr(frontdoor, "_workflow_main_with_state_lease", lambda _args, *, workflow_runner: workflow_runner(_args))
    def cli_main(args, *, fleet_provisioner):
        assert fleet_provisioner is owner
        assert args[-2:] == ["--fleet-run-id", "run-a"]
        print("Fleet Run ID: run-a")
        return 0
    monkeypatch.setattr(cli, "main", cli_main)
    assert frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a") == 0
    assert seen == ["Deployment certification control state differs from canonical bootstrap", None]


def test_source_context_mismatch_refuses_before_any_bootstrap_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(workflow, "_backend_cli", lambda *_a, **_kw: calls.append("bootstrap"))
    monkeypatch.setattr(workflow, "_stream", lambda *_a, **_kw: pytest.fail("subprocess"))
    rc, msg = workflow._run_plan(
        plan=workflow.APP_PLAN, repo_id="r", env={"OH_GOON_DB": "changed"},
        lock_root=Path("/tmp/locks"),
        deployment_context_guard=lambda _env: "mismatched database",
        deployment_plan_run=lambda *_a: pytest.fail("plan run"),
    )
    assert rc == 2 and msg == "mismatched database"
    assert calls == []


def test_deployment_never_runs_release_or_self_verify(monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_backend_cli", lambda *_a, **_kw: pytest.fail("bootstrap"))
    for plan in (workflow.RELEASE_PLAN, workflow.SELF_VERIFY_PLAN):
        rc, _msg = workflow._run_plan(
            plan=plan, repo_id="r", env={}, lock_root=Path("/tmp"),
            deployment_context_guard=lambda _env: None,
            deployment_plan_run=lambda *_a: pytest.fail("plan run"),
        )
        assert rc == 2


def test_plan_cli_transport_only_after_existing_canonical_lock(monkeypatch) -> None:
    commands = []
    monkeypatch.setattr(workflow, "_backend_cli", lambda args, **_kw: (commands.append(args), SimpleNamespace(returncode=0, stdout=""))[1])
    monkeypatch.setattr(workflow, "_stream", lambda *_a, **_kw: pytest.fail("subprocess Plan"))
    plans = []
    rc, out = workflow._run_plan(
        plan=workflow.APP_PLAN, repo_id="r", env={}, lock_root=Path("/tmp/locks"),
        deployment_context_guard=lambda _env: None,
        deployment_plan_run=lambda args, _env: (plans.append(args), (0, "Fleet Run ID: run-a\n"))[1],
    )
    assert rc == 0 and "Fleet Run ID: run-a" in out
    assert [c[0] for c in commands] == ["league", "lock"]
    assert plans[0][:3] == ["plan", "run", workflow.APP_PLAN]
    assert plans[0][plans[0].index("--lock-file") + 1] == "/tmp/locks/app-certification.goon.lock"


def test_callback_success_cannot_invent_app_pass(monkeypatch, capsys) -> None:
    monkeypatch.setattr(workflow, "_prepare_plan_context", lambda **_kw: ("r", {}, Path("/tmp")))
    monkeypatch.setattr(workflow, "_run_plan", lambda **_kw: (0, "Fleet Run ID: forged\n"))
    monkeypatch.setattr(workflow, "_require_current_app_pass", lambda **_kw: SimpleNamespace(returncode=3))
    assert workflow._run_self_plan(
        "certify", deployment_plan_run=Mock(), deployment_context_guard=Mock(),
        deployment_fleet_run_id="run-a",
    ) == 2
    assert "APP CERTIFICATION PASSED" not in capsys.readouterr().out


def test_original_persisted_app_pass_is_required_after_plan_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(workflow, "_prepare_plan_context", lambda **_kw: ("r", {}, Path("/tmp")))
    monkeypatch.setattr(workflow, "_run_plan", lambda **_kw: (0, "Fleet Run ID: run-a\n"))
    checked = []
    monkeypatch.setattr(workflow, "_require_current_app_pass", lambda **kw: (checked.append(kw), SimpleNamespace(returncode=0))[1])
    assert workflow._run_self_plan(
        "certify", deployment_plan_run=Mock(), deployment_context_guard=lambda _env: None,
        deployment_fleet_run_id="run-a",
    ) == 0
    assert len(checked) == 1
    assert checked[0]["fleet_run_id"] == "run-a"
    assert "APP CERTIFICATION PASSED" in capsys.readouterr().out


def test_native_deployment_observation_denial_blocks_state_lease(tmp_path, monkeypatch) -> None:
    owner = _original_owner(monkeypatch)
    monkeypatch.setenv(EXECUTORS_FILE_ENV, str(owner.executor_inventory_file))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    _canonical_control_state(tmp_path, monkeypatch)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(frontdoor, "_workflow_main_with_state_lease", lambda *_a, **_kw: pytest.fail("lease"))
    def revoked(_self, _run_id):
        raise TrustProtocolError("deployment signer policy revoked")
    monkeypatch.setattr(FleetPreworkProvisioner, "inspect_deployment_readiness", revoked)
    with pytest.raises(TrustProtocolError, match="revoked"):
        frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="run-a")


def test_normal_shell_main_cannot_pass_owner_through_arguments(monkeypatch) -> None:
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: pytest.fail("source admission"))
    assert frontdoor.main(["--fleet-provisioner", "forged"]) == 2
