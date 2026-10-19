"""M211: diagnostic preflight reuses original native authority, never admits a Run."""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_DELETE, SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.services.certification_workspace_retention import certification_state_root
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def _forbid_work(monkeypatch):
    monkeypatch.setattr(
        frontdoor, "_static_source_admission", lambda: pytest.fail("source admission")
    )
    monkeypatch.setattr(
        frontdoor, "_workflow_main_with_state_lease", lambda *_a, **_kw: pytest.fail("lease")
    )


def test_original_enrolled_preflight_is_read_only_and_not_app_pass(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    _forbid_work(monkeypatch)
    try:
        result = frontdoor.inspect_deployment_certification_readiness(
            fleet_provisioner=owner,
            fleet_run_id="durable-run",
        )
        assert type(result) is MappingProxyType
        assert result == owner.inspect_deployment_readiness("durable-run")
        assert result["status"] == "OBSERVED_NOT_ADMITTED"
        assert result["app_certification"] == "NOT_ESTABLISHED"
        assert result["run_admission"] == "NOT_PERFORMED"
        assert result["authenticated_replay"] == "NOT_AUTHORIZED"
        assert result["physical_containment"] == "NOT_ESTABLISHED"
        assert binding.configured.executor_id == result["executor_id"]
        assert "trusted_key_directory" not in str(dict(result))
        with pytest.raises(TypeError):
            result["status"] = "ADMITTED"
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "fake-owner",
        "unenrolled",
        "missing-inventory-env",
        "wrong-inventory-env",
        "missing-journal",
        "wrong-journal",
        "changed-generation",
        "revoked-signer",
        "changed-root",
    ],
)
def test_preflight_refuses_untrusted_owner_or_environment_without_work(
    tmp_path, monkeypatch, attack
):
    args, _params, conn, calls, _lifecycle, binding, owner, inventory, roots, _admit = provisioned(
        tmp_path, monkeypatch
    )
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    _forbid_work(monkeypatch)
    try:
        run_id = "durable-run"
        if attack == "fake-owner":
            owner = object()
        elif attack == "unenrolled":
            run_id = "missing-run"
        elif attack == "missing-inventory-env":
            monkeypatch.delenv(EXECUTORS_FILE_ENV, raising=False)
        elif attack == "wrong-inventory-env":
            monkeypatch.setenv(EXECUTORS_FILE_ENV, str(tmp_path / "fake.toml"))
        elif attack == "missing-journal":
            monkeypatch.delenv(SQLITE_JOURNAL_MODE_ENV, raising=False)
        elif attack == "wrong-journal":
            monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, "WAL")
        elif attack == "changed-generation":
            inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
        elif attack == "revoked-signer":
            path = binding.key_directory / "signers.json"
            data = json.loads(path.read_bytes())
            data["issuers"]["source_identity"]["state"] = "revoked"
            path.write_bytes(canonical_bytes(data))
        elif attack == "changed-root":
            (roots / "source_identity.pub").write_bytes(b"x" * 32)
        with pytest.raises((TrustProtocolError, FleetPlanInvalid)):
            frontdoor.inspect_deployment_certification_readiness(
                fleet_provisioner=owner,
                fleet_run_id=run_id,
            )
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_real_owner_certification_reuses_same_context_and_observer_after_source(
    tmp_path, monkeypatch
):
    args, _params, conn, calls, _lifecycle, _binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    from oh_goon import workflow

    state = certification_state_root(source_root=workflow.ROOT, state_home=tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("OH_GOON_DB", str(state / "oh-goon.db"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(state / "league.yml"))
    sequence = []
    monkeypatch.setattr(
        frontdoor, "_static_source_admission", lambda: sequence.append("static") or True
    )
    original_observer = FleetPreworkProvisioner.inspect_deployment_readiness

    def native_observe(self, run_id):
        assert self is owner
        sequence.append("original-native-observer")
        return original_observer(self, run_id)

    monkeypatch.setattr(FleetPreworkProvisioner, "inspect_deployment_readiness", native_observe)
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda *_a, **_kw: sequence.append("state-lease") or 2,
    )
    try:
        assert (
            frontdoor.certify_deployment_run(
                fleet_provisioner=owner,
                fleet_run_id="durable-run",
            )
            == 2
        )
        assert sequence == ["static", "original-native-observer", "state-lease"]
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_live_signer_revocation_after_readonly_preflight_denies_before_state(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, owner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    monkeypatch.setenv(SQLITE_JOURNAL_MODE_ENV, SQLITE_JOURNAL_DELETE)
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(
        frontdoor, "_workflow_main_with_state_lease", lambda *_a, **_kw: pytest.fail("state work")
    )
    try:
        assert (
            frontdoor.inspect_deployment_certification_readiness(
                fleet_provisioner=owner,
                fleet_run_id="durable-run",
            )["status"]
            == "OBSERVED_NOT_ADMITTED"
        )
        signer = binding.key_directory / "signers.json"
        policy = json.loads(signer.read_bytes())
        policy["issuers"]["source_identity"]["state"] = "revoked"
        signer.write_bytes(canonical_bytes(policy))
        with pytest.raises((TrustProtocolError, FleetPlanInvalid)):
            frontdoor.certify_deployment_run(
                fleet_provisioner=owner,
                fleet_run_id="durable-run",
            )
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()
