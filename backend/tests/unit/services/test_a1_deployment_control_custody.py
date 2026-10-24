"""A1: original certification state selection must fail before state/effects."""

from __future__ import annotations

import json
import os

import pytest

from oh_goon import workflow
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_DELETE, SQLITE_JOURNAL_MODE_ENV
from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.services.certification_workspace_retention import certification_state_root
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _env(tmp_path, monkeypatch):
    state = certification_state_root(source_root=workflow.ROOT, state_home=tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("OH_GOON_DB", str(state / "oh-goon.db"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(state / "league.yml"))
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    return state


@pytest.mark.parametrize(
    "attack",
    [
        "missing-db",
        "different-db",
        "relative-db",
        "missing-league",
        "different-league",
        "new-state-home",
        "revoked-signer",
    ],
)
def test_wrong_certification_selection_rejected_before_state_lease(
    tmp_path,
    monkeypatch,
    attack,
):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _ = provisioned(
        tmp_path, monkeypatch
    )
    state = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda *_a, **_kw: pytest.fail("mutable state lease"),
    )
    observed = []
    observer = type(owner).inspect_deployment_readiness

    def seen(self, run_id):
        observed.append(run_id)
        return observer(self, run_id)

    monkeypatch.setattr(type(owner), "inspect_deployment_readiness", seen)
    if attack == "missing-db":
        monkeypatch.delenv("OH_GOON_DB")
    elif attack == "different-db":
        monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "foreign.db"))
    elif attack == "relative-db":
        monkeypatch.setenv("OH_GOON_DB", "oh-goon.db")
    elif attack == "missing-league":
        monkeypatch.delenv("OH_GOON_LEAGUE_FILE")
    elif attack == "different-league":
        monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "other.yml"))
    elif attack == "new-state-home":
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "other-state"))
    elif attack == "revoked-signer":
        signer = binding.key_directory / "signers.json"
        policy = json.loads(signer.read_bytes())
        policy["issuers"]["source_identity"]["state"] = "revoked"
        signer.write_bytes(canonical_bytes(policy))

    try:
        with pytest.raises(TrustProtocolError):
            frontdoor.certify_deployment_run(
                fleet_provisioner=owner,
                fleet_run_id="durable-run",
            )
        assert observed == (["durable-run"] if attack == "revoked-signer" else [])
        assert not state.exists(), "selection alone must not create certification state"
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_native_observation_cannot_change_control_path_before_lease(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, _binding, owner, _inventory, _roots, _ = provisioned(
        tmp_path, monkeypatch
    )
    state = _env(tmp_path, monkeypatch)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda *_a, **_kw: pytest.fail("mutable state lease"),
    )
    original = type(owner).inspect_deployment_readiness

    def racing_observer(self, run_id):
        result = original(self, run_id)
        monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "replaced.db"))
        return result

    monkeypatch.setattr(type(owner), "inspect_deployment_readiness", racing_observer)
    try:
        with pytest.raises(TrustProtocolError, match="control state"):
            frontdoor.certify_deployment_run(fleet_provisioner=owner, fleet_run_id="durable-run")
        assert not state.exists()
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_original_in_process_app_transport_keeps_one_owner_and_canonical_identity(
    tmp_path, monkeypatch
):
    args, _params, conn, calls, _lifecycle, _binding, owner, _inventory, _roots, _ = provisioned(
        tmp_path, monkeypatch
    )
    state = _env(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(
        frontdoor, "_static_source_admission", lambda: events.append("source") or True
    )
    from oh_goon import cli

    def delegated_plan(argv, *, fleet_provisioner):
        assert fleet_provisioner is owner
        assert argv == [
            "dev",
            "_run-plan",
            workflow.APP_PLAN,
            "--repository-id",
            "repo-example",
            "--lock-file",
            str(state / "locks" / "app-certification.goon.lock"),
            "--wait",
            "--repair-handoff",
            "--fleet-run-id",
            "durable-run",
        ]
        events.append("original-cli")
        return 2  # No forged APP success.

    def original_workflow(*, fleet_run_id, deployment_context_guard, deployment_plan_run):
        assert fleet_run_id == "durable-run"
        env = dict(os.environ)
        assert deployment_context_guard(env) is None
        events.append("original-workflow")
        status, message = deployment_plan_run(
            [
                "dev",
                "_run-plan",
                workflow.APP_PLAN,
                "--repository-id",
                "repo-example",
                "--lock-file",
                str(state / "locks" / "app-certification.goon.lock"),
                "--wait",
                "--repair-handoff",
            ],
            env,
        )
        assert message == ""
        return status

    monkeypatch.setattr(cli, "main", delegated_plan)
    monkeypatch.setattr(workflow, "run_app_for_deployment", original_workflow)
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda _a, *, workflow_runner: events.append("lease-cut") or workflow_runner(["app"]),
    )
    try:
        assert (
            frontdoor.certify_deployment_run(
                fleet_provisioner=owner,
                fleet_run_id="durable-run",
            )
            == 2
        )
        assert events == ["source", "lease-cut", "original-workflow", "original-cli"]
        assert not state.exists()
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()
