"""M161: native deployment executor owner, strict Run placement, issuer isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement
from oh_goon.core.models.run_admission import FleetRunAdmissionIntent, StrictFleetExecutionAdmission
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.repository_tooling import enforcer_lace_execution_environment_issuer as issuer
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.journal import admit_run, start_run
from tests.unit.services.test_group_a_native_tool_issuer import fixture as tool_fixture
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
)


def fixture(tmp_path: Path, monkeypatch):
    old_journal, old_contract, authority, _ = tool_fixture(tmp_path, monkeypatch)
    original = old_journal.read_view().run_events("durable-run").events[0]
    bwrap = tmp_path / "bwrap"
    bwrap.write_bytes(b"#!/bin/sh\nexit 0\n")
    bwrap.chmod(0o755)
    inventory = tmp_path / "executors.toml"
    inventory.write_text(f'''version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "{authority.runtime_inspector}"
trusted_host_roots = ["{authority.trusted_roots[0]}"]
tool_search_paths = ["{authority.tool_search_paths[0]}"]
[[executors]]
id = "worker-m161"
generation = 7
execution = {{ environment = {{ backend = "bubblewrap" }} }}
bwrap_executable = "{bwrap}"
priority = 1
cpu_millis = 2000
memory_mib = 2048
cgroup_root = "/sys/fs/cgroup/oh-goon"
''')
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(inventory))
    pool = configured_executor_pool()
    worker = pool.executors[0]
    evidence = isolation_backend_evidence(worker.backend)
    old_capacity = original.execution.placement.executor_capacity
    capacity = old_capacity.model_copy(
        update={
            "executor_id": worker.identity.executor_id,
            "executor_generation": worker.identity.generation,
            "backend_configuration_id": evidence.configuration_id,
            "capacity_cpu_millis": worker.capacity.cpu_millis,
            "capacity_memory_bytes": worker.capacity.memory_mib * 1024 * 1024,
            "capacity_max_processes": worker.capacity.max_processes,
        }
    )
    execution = StrictFleetExecutionAdmission(
        placement=FleetExecutorPlacement(
            isolation_backend=evidence, executor_capacity=capacity, executor_trust_epoch=0
        )
    )
    intent = FleetRunAdmissionIntent(
        source=original.source,
        target=original.target,
        capability=original.capability,
        execution=execution,
        repository_admissions=original.repository_admissions,
        application_launch=original.application_launch,
        scheduling=original.scheduling,
    )
    journal = Journal(LocalDatabase(tmp_path / "env-run.sqlite3"))
    admit_run(journal, "durable-run", intent, original.repository_scope, list(original.targets))
    fields = old_contract.fields
    fields["execution_environment_identity"] = worker.resolve_environment(
        pool.path_authority
    ).execution_environment_identity
    fields["executor_generation"] = worker.identity.generation
    return journal, AdmittedRunContract.freeze(fields), inventory, bwrap


def sign(journal, contract):
    return issuer.issue_observed_captured_run_execution_environment_identity(
        journal=journal,
        contract=contract,
        issuer_id=roots()[issuer.ENV_FIELD][0],
        signing_key=_test_private_key(issuer.ENV_FIELD),
    )


def test_deployment_selected_strict_executor_has_independently_verifiable_signature(
    tmp_path, monkeypatch
):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
    signed = sign(journal, contract)
    assert signed == sign(journal, contract)
    proofs = tuple(
        signed if field == issuer.ENV_FIELD else proof(field, contract) for field in FIELDS
    )
    TrustedIssuerVerifier(roots()).verify(
        contract,
        evidence=proofs,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    assert (
        issuer.observed_admitted_run_execution_environment_identity(
            journal=journal, run_id="durable-run"
        )
        == contract.fields[issuer.ENV_FIELD]
    )


def test_modified_backend_inventory_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
    with pytest.raises(TrustProtocolError, match="placement differs"):
        sign(journal, contract)


def test_backend_executable_replacement_denied(tmp_path, monkeypatch):
    journal, contract, _, executable = fixture(tmp_path, monkeypatch)
    executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(TrustProtocolError, match="placement differs"):
        sign(journal, contract)


def test_missing_and_alias_inventory_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE")
    with pytest.raises(TrustProtocolError, match="inventory required"):
        sign(journal, contract)
    alias = inventory.parent / "alias.toml"
    alias.symlink_to(inventory)
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(alias))
    with pytest.raises(TrustProtocolError, match="canonical file"):
        sign(journal, contract)


def test_wrong_contract_and_generation_denied(tmp_path, monkeypatch):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
    fields = contract.fields
    fields[issuer.ENV_FIELD] = "sha256:" + "f" * 64
    with pytest.raises(TrustProtocolError, match="contract Execution Environment"):
        sign(journal, AdmittedRunContract.freeze(fields))
    fields = contract.fields
    fields["executor_generation"] += 1
    with pytest.raises(TrustProtocolError, match="contract Execution Environment"):
        sign(journal, AdmittedRunContract.freeze(fields))


def test_started_run_denied_before_signing(tmp_path, monkeypatch):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
    start_run(journal, "durable-run")
    with pytest.raises(TrustProtocolError, match="sole committed admission"):
        sign(journal, contract)


def test_missing_issuer_denied(tmp_path, monkeypatch):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(TrustProtocolError, match="signing authority"):
        issuer.issue_observed_captured_run_execution_environment_identity(
            journal=journal, contract=contract, issuer_id="test", signing_key=None
        )


def test_inventory_changed_between_reads_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    original = issuer._observe
    calls = 0

    def race(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            inventory.write_text(inventory.read_text() + "\n# changed after first read\n")
        return result

    monkeypatch.setattr(issuer, "_observe", race)
    with pytest.raises(TrustProtocolError, match="changed during signing"):
        sign(journal, contract)


def test_run_started_during_issuance_denied(tmp_path, monkeypatch):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
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


def test_capacity_change_denied_even_with_same_backend_generation(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    inventory.write_text(inventory.read_text().replace("memory_mib = 2048", "memory_mib = 4096"))
    with pytest.raises(TrustProtocolError, match="placement differs"):
        sign(journal, contract)


def test_hardlink_inventory_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    (inventory.parent / "shadow").hardlink_to(inventory)
    with pytest.raises(TrustProtocolError, match="canonical file"):
        sign(journal, contract)


def test_direct_execution_is_not_assumed_to_have_strict_executor(tmp_path, monkeypatch):
    journal, contract, _, _ = fixture(tmp_path, monkeypatch)
    from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission

    original = issuer._read_original_admission

    def changed(journal, run_id):
        admitted, revision = original(journal, run_id)
        return admitted.model_copy(update={"execution": DirectFleetExecutionAdmission()}), revision

    monkeypatch.setattr(issuer, "_read_original_admission", changed)
    with pytest.raises(TrustProtocolError, match="strict durable executor placement"):
        sign(journal, contract)


def test_missing_admitted_executor_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    inventory.write_text(inventory.read_text().replace('id = "worker-m161"', 'id = "other"'))
    with pytest.raises(TrustProtocolError, match="absent from deployment"):
        sign(journal, contract)


def test_implicit_backend_fallback_denied(tmp_path, monkeypatch):
    journal, contract, inventory, _ = fixture(tmp_path, monkeypatch)
    inventory.write_text(
        inventory.read_text().replace(
            'execution = { environment = { backend = "bubblewrap" } }',
            'execution = { environment = { backend = "auto" } }',
        )
    )
    with pytest.raises(TrustProtocolError, match="owner is unavailable"):
        sign(journal, contract)
