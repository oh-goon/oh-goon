"""M162: backend-owner manifest authentication, not simulated physical enforcement."""

from __future__ import annotations

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.repository_tooling import enforcer_lace_backend_capability_issuer as issuer
from oh_goon.repository_tooling import enforcer_lace_execution_environment_issuer as env_owner
from tests.factories.journal import start_run
from tests.unit.services.test_group_a_execution_environment_issuer import fixture
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    _test_private_key,
    proof,
    roots,
)


def prepared(tmp_path, monkeypatch):
    journal, contract, inventory, bwrap = fixture(tmp_path, monkeypatch)
    observed = env_owner._observe(journal, "durable-run", control_state_root=None)
    fields = contract.fields
    fields[issuer.CAPABILITY_FIELD] = observed[4].capability_manifest_identity
    return journal, AdmittedRunContract.freeze(fields), inventory, bwrap


def sign(journal, contract, **kw):
    return issuer.issue_observed_captured_run_backend_capability_manifest_identity(
        journal=journal,
        contract=contract,
        issuer_id=roots()[issuer.CAPABILITY_FIELD][0],
        signing_key=_test_private_key(issuer.CAPABILITY_FIELD),
        **kw,
    )


def test_native_backend_manifest_signed_by_independent_key(tmp_path, monkeypatch):
    journal, contract, _, _ = prepared(tmp_path, monkeypatch)
    signed = sign(journal, contract)
    witnesses = [
        proof(f, contract) if f != issuer.CAPABILITY_FIELD else signed for f in sorted(roots())
    ]
    TrustedIssuerVerifier(roots()).verify(
        contract,
        evidence=witnesses,
        control_epoch=1,
        lease_identity="exact",
        lease_is_current=lambda *_: True,
    )
    assert (
        issuer.observed_admitted_run_backend_capability_manifest_identity(
            journal=journal,
            run_id="durable-run",
        )
        == contract.fields[issuer.CAPABILITY_FIELD]
    )


def test_wrong_manifest_or_environment_cannot_be_signed(tmp_path, monkeypatch):
    journal, contract, _, _ = prepared(tmp_path, monkeypatch)
    fields = contract.fields
    fields[issuer.CAPABILITY_FIELD] = "sha256:" + "f" * 64
    with pytest.raises(TrustProtocolError, match="contract capability manifest"):
        sign(journal, AdmittedRunContract.freeze(fields))
    fields = contract.fields
    fields["execution_environment_identity"] = "sha256:" + "f" * 64
    with pytest.raises(TrustProtocolError, match="contract capability manifest"):
        sign(journal, AdmittedRunContract.freeze(fields))


def test_inventory_changed_or_missing_owner_rejected(tmp_path, monkeypatch):
    journal, contract, inventory, _ = prepared(tmp_path, monkeypatch)
    inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
    with pytest.raises(TrustProtocolError, match="placement differs"):
        sign(journal, contract)
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE")
    with pytest.raises(TrustProtocolError, match="inventory required"):
        sign(journal, contract)


def test_backend_content_changed_or_inventory_alias_rejected(tmp_path, monkeypatch):
    journal, contract, inventory, bwrap = prepared(tmp_path, monkeypatch)
    bwrap.write_text("#!/bin/sh\nexit 88\n")
    with pytest.raises(TrustProtocolError, match="placement differs"):
        sign(journal, contract)
    bwrap.write_text("#!/bin/sh\nexit 0\n")
    alias = inventory.parent / "alias.toml"
    alias.symlink_to(inventory)
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(alias))
    with pytest.raises(TrustProtocolError, match="canonical file"):
        sign(journal, contract)


def test_lifecycle_and_invalid_signer_rejected(tmp_path, monkeypatch):
    journal, contract, _, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(TrustProtocolError, match="signing authority"):
        issuer.issue_observed_captured_run_backend_capability_manifest_identity(
            journal=journal, contract=contract, issuer_id="x", signing_key=None
        )
    start_run(journal, "durable-run")
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        sign(journal, contract)


def test_race_inventory_replacement_between_observations(tmp_path, monkeypatch):
    journal, contract, inventory, _ = prepared(tmp_path, monkeypatch)
    original = issuer._observe
    calls = 0

    def race(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            inventory.write_text(inventory.read_text() + "\n# capability race\n")
        return result

    monkeypatch.setattr(issuer, "_observe", race)
    with pytest.raises(TrustProtocolError, match="changed during signing"):
        sign(journal, contract)


def test_race_run_start_between_observations(tmp_path, monkeypatch):
    journal, contract, _, _ = prepared(tmp_path, monkeypatch)
    original = issuer._observe
    calls = 0

    def race(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            start_run(journal, "durable-run")
        return result

    monkeypatch.setattr(issuer, "_observe", race)
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        sign(journal, contract)


def test_native_capability_declaration_change_rejected(tmp_path, monkeypatch):
    journal, contract, _, _ = prepared(tmp_path, monkeypatch)
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

    current = BubblewrapProcessIsolation.capabilities
    monkeypatch.setattr(
        BubblewrapProcessIsolation,
        "capabilities",
        current.model_copy(update={"enforced_network_policy": False}),
    )
    with pytest.raises(TrustProtocolError, match="contract capability manifest"):
        sign(journal, contract)


def test_same_inventory_file_replaced_during_resolution_fails(tmp_path, monkeypatch):
    journal, contract, inventory, _ = prepared(tmp_path, monkeypatch)
    original = issuer.configured_executor_pool

    def replaced(*args, **kwargs):
        result = original(*args, **kwargs)
        replacement = inventory.with_suffix(".new")
        replacement.write_bytes(inventory.read_bytes())
        replacement.replace(inventory)
        return result

    monkeypatch.setattr(issuer, "configured_executor_pool", replaced)
    with pytest.raises(TrustProtocolError, match="executor inventory changed"):
        sign(journal, contract)
