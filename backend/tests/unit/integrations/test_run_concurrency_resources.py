"""Resource intents bind one original native budget and never issue public claims."""

from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import _fleet_plan_cgroup_resources as native_cgroups
from oh_goon.integrations import run_resource_envelope as resources
from tests.factories.run_resources import native_runtime_slice_evidence as aggregate
from tests.factories.run_resources import run_concurrency_policy as policy


@pytest.fixture
def deferred(monkeypatch):
    budget = resources.ResolvedNativeRuntimeBudget(
        policy(),
        aggregate(),
        sys.executable,
        "a" * 64,
        sys.executable,
        "b" * 64,
        (("XDG_RUNTIME_DIR", "/captured/native-manager"),),
    )
    monkeypatch.setattr(resources, "require_native_runtime_budget_current", lambda *a, **kw: None)
    monkeypatch.setattr(
        native_cgroups,
        "standard_resource_scope_command",
        lambda _policy, executable, **kw: (
            executable,
            "--user",
            "--scope",
            "--collect",
            f"--slice={kw['target_slice']}",
            "--",
        ),
    )
    return resources.prepare_worker_resource_scope("original-run", budget)


def _envelope(deferred) -> RunResourceEnvelopeEvidence:
    birth = observe_process_birth()
    assert birth is not None
    return RunResourceEnvelopeEvidence(
        subject="worker",
        fleet_run_id=deferred.scope.fleet_run_id,
        unit_name=deferred.scope.unit_name,
        invocation_id="c" * 32,
        handoff_sha256="sha256:" + "d" * 64,
        process_birth=birth,
        resources=deferred.scope.resources,
        cgroup_device=1,
        cgroup_inode=2,
        cpu_max="75000 100000",
        memory_max_bytes=512 * 1024 * 1024,
        memory_swap_max_bytes=0,
        pids_max=256,
        oom_policy="kill",
        memory_oom_group=True,
    )


def test_deferred_scope_transport_retains_resolved_manager_and_exact_admission(
    deferred, monkeypatch
):
    before = deferred.to_payload()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/unrelated/ambient")
    restored = resources.DeferredWorkerResourceScope.from_payload(before)
    assert restored == deferred
    assert restored.scope.environment == {"XDG_RUNTIME_DIR": "/captured/native-manager"}
    assert restored.admission.budget_identity == restored.budget.identity
    assert f"--slice={restored.budget.policy.aggregate_slice}" in restored.scope.command_prefix


@pytest.mark.parametrize("changed", ["run", "unit", "slice", "environment", "journal-tool"])
def test_scope_substitution_cannot_reuse_original_admission(deferred, changed):
    payload = deferred.to_payload()
    if changed == "run":
        payload["scope"]["fleet_run_id"] = "foreign-run"
    elif changed == "unit":
        payload["scope"]["unit_name"] = "oh-goon-run-foreign.scope"
    elif changed == "slice":
        payload["scope"]["target_slice"] = "foreign.slice"
    elif changed == "environment":
        payload["scope"]["manager_environment"] = []
    else:
        payload["budget"]["journalctl"] = "/foreign/journalctl"
        payload["budget"]["journalctl_sha256"] = "f" * 64
    with pytest.raises(FleetPlanInvalid):
        resources.DeferredWorkerResourceScope.from_payload(payload)


def test_passive_envelope_cannot_issue_native_claim_and_current_process_is_bound(
    deferred, monkeypatch
):
    envelope = _envelope(deferred)
    with pytest.raises(FleetPlanInvalid, match="does not match"):
        resources.VerifiedWorkerResourceClaim(
            deferred.admission, envelope, object()
        ).require_matches(deferred.admission, envelope)
    monkeypatch.setattr(resources, "observe_worker_resource_scope", lambda *a, **kw: envelope)
    claim = resources.verify_worker_resource_claim(deferred, envelope)
    claim.require_matches(deferred.admission, envelope)
    with pytest.raises(FleetPlanInvalid):
        claim.require_matches(
            deferred.admission.model_copy(update={"fleet_run_id": "foreign"}), envelope
        )
    monkeypatch.setattr(resources, "observe_process_birth", lambda *a: None)
    with pytest.raises(FleetPlanInvalid):
        claim.require_matches(deferred.admission, envelope)


def test_unknown_original_scope_does_not_release_resource_reservation(deferred):
    with pytest.raises(FleetPlanInvalid, match="unproven"):
        resources.finish_worker_resource_scope(deferred, None, pinned=None)
    with pytest.raises(FleetPlanInvalid, match="unproven"):
        resources.finish_worker_resource_scope(deferred, _envelope(deferred), pinned=None)


@pytest.fixture
def recovered_scope(deferred, monkeypatch, tmp_path):
    aggregate = deferred.budget.aggregate.model_copy(
        update={
            "manager_birth": ProcessBirthIdentity(pid=7, boot_id="native-boot", start_ticks=3),
            "manager_executable": "/trusted/systemd",
            "manager_executable_artifact_sha256": "a" * 64,
            "manager_version": "259.9-fixture",
            "manager_uid": 1000,
        }
    )
    budget = replace(
        deferred.budget, aggregate=aggregate, journalctl=sys.executable, journalctl_sha256="b" * 64
    )
    deferred = resources.DeferredWorkerResourceScope.create(deferred.scope, budget)
    envelope = _envelope(deferred).model_copy(
        update={
            "process_birth": ProcessBirthIdentity(pid=8, boot_id="native-boot", start_ticks=4),
            "control_group": f"/qualified.slice/{deferred.scope.unit_name}",
        }
    )
    kernel = tmp_path / "kernel"
    (kernel / "qualified.slice").mkdir(parents=True)
    original = kernel / "qualified.slice" / deferred.scope.unit_name
    monkeypatch.setattr(resources, "observe_process_birth", lambda *args: None)
    monkeypatch.setattr(resources, "observe_process_liveness", lambda *args: False)
    monkeypatch.setattr(
        resources, "_slice_properties", lambda *args: {"ControlGroup": "/qualified.slice"}
    )
    monkeypatch.setattr(
        native_cgroups,
        "_standard_cgroup_path",
        lambda root, relative, **kwargs: kernel / relative.lstrip("/"),
    )
    properties = {
        "Id": deferred.scope.unit_name,
        "LoadState": "not-found",
        "ActiveState": "inactive",
        "InvocationID": "",
        "ControlGroup": "",
        "Transient": "no",
    }
    monkeypatch.setattr(resources, "_query", lambda *args: properties)
    record = {
        "MESSAGE_ID": "ae8f7b866b0347b9af31fe1c80b127c0",
        "USER_UNIT": envelope.unit_name,
        "USER_INVOCATION_ID": envelope.invocation_id,
        "_UID": "1000",
        "_PID": "7",
        "_COMM": "systemd",
        "_EXE": "/trusted/systemd",
        "_SYSTEMD_UNIT": "user@1000.service",
        "_SYSTEMD_USER_UNIT": "init.scope",
        "_BOOT_ID": "nativeboot",
        "__CURSOR": "exact-native-cursor",
        "__MONOTONIC_TIMESTAMP": "42",
    }
    return deferred, envelope, properties, record, original


def test_restart_native_witness_requires_joined_manager_record_and_gc(recovered_scope, monkeypatch):
    deferred, envelope, _properties, record, _original = recovered_scope
    monkeypatch.setattr(
        resources,
        "run_command",
        lambda *args, **kwargs: CommandResult(0, json.dumps(record) + "\n", ""),
    )
    proof = resources.recover_worker_resource_scope(deferred, envelope)
    proof.require_matches(deferred.admission, envelope)
    assert proof.observation.cleanup == "VERIFIED"
    assert proof.observation.native_witness == "original_systemd_journal"
    assert proof.observation.state == "INCOMPLETE"
    assert proof.observation.native_witness_identity


@pytest.mark.parametrize(
    "negative",
    [
        "missing",
        "truncated",
        "foreign-invocation",
        "foreign-process",
        "raw-prose",
        "duplicate",
        "retained-unit",
        "present-cgroup",
        "manager-changed",
    ],
)
def test_incomplete_restart_metadata_never_issues_resource_closure(
    recovered_scope, monkeypatch, negative
):
    deferred, envelope, properties, record, original = recovered_scope
    output = json.dumps(record) + "\n"
    truncated = False
    if negative == "missing":
        output = ""
    elif negative == "truncated":
        truncated = True
    elif negative == "foreign-invocation":
        record["USER_INVOCATION_ID"] = "f" * 32
        output = json.dumps(record) + "\n"
    elif negative == "foreign-process":
        record["_PID"] = "9"
        output = json.dumps(record) + "\n"
    elif negative == "raw-prose":
        output = json.dumps({"MESSAGE": "Unit stopped successfully"}) + "\n"
    elif negative == "duplicate":
        output *= 2
    elif negative == "retained-unit":
        properties["LoadState"] = "loaded"
    elif negative == "present-cgroup":
        original.mkdir()
    else:

        def changed(*args, **kwargs):
            raise FleetPlanInvalid("Original manager changed")

        monkeypatch.setattr(resources, "require_native_runtime_budget_current", changed)
    monkeypatch.setattr(
        resources,
        "run_command",
        lambda *args, **kwargs: CommandResult(0, output, "", stdout_truncated=truncated),
    )
    with pytest.raises(FleetPlanInvalid):
        resources.recover_worker_resource_scope(deferred, envelope)
