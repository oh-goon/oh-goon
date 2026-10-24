"""B12C pre-effect bridge regression: no physical work without committed original intent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_local_resource_intent import LocalLaceJournalReservation
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _store


def _request(tmp_path: Path):
    root = tmp_path / "attempt-one"
    attempt = SimpleNamespace(
        paths=SimpleNamespace(
            root=root,
            workspace=root / "workspace",
            cache=root / "cache",
            etc=root / "etc",
            home=root / "home",
            tmp=root / "tmp",
            config=root / "config",
        ),
        attempt_number=1,
        runtime_root=SimpleNamespace(
            root=root / "runtime", identity=SimpleNamespace(sha256="a" * 64), uv_system_python=None
        ),
        egress=SimpleNamespace(model_dump=lambda **_: {"mode": "none"}),
        resources=SimpleNamespace(
            model_dump=lambda **_: {"cpu_millis": 400, "memory_mib": None, "max_processes": None}
        ),
        nested_strict_execution=False,
        sandbox_bindings=(),
        host_tools=(),
    )
    return SimpleNamespace(
        attempt=attempt,
        fleet_run_id="run-one",
        step_id="step-1",
        command_ordinal=1,
        cwd=root / "workspace",
        local_resource_intent=None,
        termination_requested=None,
        read_only_workspace=None,
        agent_source_mount=None,
    )


def _backend(monkeypatch):
    import oh_goon.integrations.fleet_plan_bubblewrap_isolation as owner

    backend = BubblewrapProcessIsolation("/bin/true", require_local_resource_intent=True)
    monkeypatch.setattr(backend, "configuration_identity", lambda: "chosen-bwrap-configuration")
    monkeypatch.setattr(owner, "verify_selected_backend", lambda *a: None)
    # This reservation-order test deliberately uses /bin/true instead of bwrap,
    # and aborts at mocked CgroupV2AttemptLease.create. Dedicated B27B2B2A
    # tests exercise real source-FD pinning and unsupported-bwrap rejection.
    from contextlib import nullcontext

    monkeypatch.setattr(owner, "require_bubblewrap_fd_binding", lambda *a: None)
    monkeypatch.setattr(
        owner.OriginalBindSourceLease, "pin_prepared", lambda *a: nullcontext(object())
    )
    touched = Mock(side_effect=RuntimeError("PHYSICAL_RESOURCE_CREATION_REACHED"))
    monkeypatch.setattr(owner.CgroupV2AttemptLease, "create", touched)
    return backend, touched


def _permit(tmp_path: Path, request, backend):
    conn, journal = _store(tmp_path / "journal.db")
    permit = LocalLaceJournalReservation(
        journal=journal,
        authenticator=authenticator(),
        run_id="run-one",
        step_id="step-1",
        attempt_number=1,
        command_ordinal=1,
        attempt_root=request.attempt.paths.root,
        control_epoch=1,
        lease_identity="lease-one",
        execution_environment_identity="sha256:" + "a" * 64,
        backend_capability_manifest_identity="sha256:" + "b" * 64,
        backend_configuration_identity=backend.configuration_identity(),
    )
    return conn, journal, permit


def _execute(backend, request):
    backend.execute(request, timeout=1, cancel_requested=None)


def test_required_missing_or_forged_intent_rejects_before_native_allocation(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="RESOURCE_INTENT missing"):
        _execute(backend, request)
    request.local_resource_intent = lambda: None
    with pytest.raises(FleetPlanInvalid, match="original local LACE reservation owner"):
        _execute(backend, request)
    touched.assert_not_called()


def test_authenticated_reservation_commits_before_first_native_effect(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    request.local_resource_intent = permit
    with pytest.raises(RuntimeError, match="PHYSICAL_RESOURCE_CREATION_REACHED"):
        _execute(backend, request)
    touched.assert_called_once()
    events = journal.verify("run-one", auth=authenticator())
    assert [event["kind"] for event in events] == ["RUN_ADMITTED", "RESOURCE_INTENT"]
    assert events[1]["payload"]["resources"] == local_resource_identities(request)
    with pytest.raises(CausalJournalError, match="duplicate"):
        _execute(backend, request)
    touched.assert_called_once()
    conn.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign-run",
        "foreign-step",
        "other-attempt",
        "other-command",
        "other-root",
        "changed-backend",
        "stale-epoch",
        "stale-lease",
        "wrong-signing-key",
        "tampered-history",
    ],
)
def test_mismatched_or_stale_original_authority_fails_before_any_effect(
    tmp_path, monkeypatch, mutation
):
    from dataclasses import replace

    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    if mutation == "foreign-run":
        request.fleet_run_id = "foreign"
    elif mutation == "foreign-step":
        request.step_id = "not-step-1"
    elif mutation == "other-attempt":
        request.attempt.attempt_number = 2
    elif mutation == "other-command":
        request.command_ordinal = 2
    elif mutation == "other-root":
        request.attempt.paths.root = tmp_path / "other-root"
    elif mutation == "changed-backend":
        monkeypatch.setattr(backend, "configuration_identity", lambda: "drifted-backend")
    elif mutation == "stale-epoch":
        permit = replace(permit, control_epoch=2)
    elif mutation == "stale-lease":
        permit = replace(permit, lease_identity="foreign")
    elif mutation == "wrong-signing-key":
        permit = replace(permit, authenticator=authenticator("foreign-key"))
    elif mutation == "tampered-history":
        conn.execute("UPDATE lace_run_authority SET admission_mac=?", ("0" * 64,))
    request.local_resource_intent = permit
    with pytest.raises((CausalJournalError, ValueError)):
        _execute(backend, request)
    touched.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
    conn.close()


def test_resource_projection_tracks_actual_attempt_policy_and_mounts(tmp_path):
    request = _request(tmp_path)
    before = local_resource_identities(request)
    request.attempt.resources = SimpleNamespace(model_dump=lambda **_: {"cpu_millis": 500})
    after = local_resource_identities(request)
    assert before["cgroup"] != after["cgroup"]
    assert before["workspace"] == after["workspace"]
    request.attempt.sandbox_bindings = (
        SimpleNamespace(
            snapshot_path=tmp_path / "source",
            sandbox_path=Path("/tools"),
            identity=SimpleNamespace(sha256="b" * 64),
        ),
    )
    assert local_resource_identities(request)["mounts"] != after["mounts"]


def test_two_distinct_commands_same_step_attempt_have_distinct_durable_identity(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    conn, journal, permit = _permit(tmp_path, request, backend)
    for ordinal in (1, 2):
        request.command_ordinal = ordinal
        request.local_resource_intent = replace(permit, command_ordinal=ordinal)
        with pytest.raises(RuntimeError, match="PHYSICAL_RESOURCE_CREATION_REACHED"):
            _execute(backend, request)
    events = journal.verify("run-one", auth=authenticator())
    assert [event["kind"] for event in events] == [
        "RUN_ADMITTED",
        "RESOURCE_INTENT",
        "RESOURCE_INTENT",
    ]
    assert events[1]["payload"]["attempt_identity"] != events[2]["payload"]["attempt_identity"]
    assert touched.call_count == 2
    conn.close()


def test_original_step_runner_transports_one_ordered_invocation_stream(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as owner
    from oh_goon.engine.command import CommandResult

    requests = []
    issued = []
    monkeypatch.setattr(owner, "verify_step_executable_materialization", lambda *args: "rechecked")
    attempt = SimpleNamespace(
        paths=SimpleNamespace(workspace=tmp_path / "workspace"),
        executable_materialization=object(),
    )
    step = SimpleNamespace(step_id="step-1")

    class RecordingBackend:
        def execute(self, request, **kwargs):
            requests.append(request)
            return CommandResult(returncode=0, stdout="", stderr="")

    def owner_factory(bound_step, bound_attempt, ordinal):
        assert bound_step is step and bound_attempt is attempt
        issued.append(ordinal)
        return object()

    runner = owner._isolated_command_runner(
        attempt,
        step,
        RecordingBackend(),
        termination_requested=None,
        fleet_run_id="run-one",
        local_resource_intent_for=owner_factory,
    )
    for argv in (("git", "status"), ("true",)):
        runner(*argv, cwd=tmp_path / "workspace", env={"SECRETS": "never-in-journal"})
    assert issued == [1, 2]
    assert [r.command_ordinal for r in requests] == [1, 2]
    assert all(r.fleet_run_id == "run-one" and r.step_id == "step-1" for r in requests)
    assert requests[0].local_resource_intent is not requests[1].local_resource_intent


def test_mandatory_local_lace_policy_is_part_of_selected_backend_identity():
    old = BubblewrapProcessIsolation("/bin/true", require_local_resource_intent=False)
    required = BubblewrapProcessIsolation("/bin/true", require_local_resource_intent=True)
    assert old.configuration_identity() != required.configuration_identity()


def test_fake_commit_proof_does_not_enable_resource_creation(tmp_path, monkeypatch):
    from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof

    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)

    class FakeReservation:
        def reserve(self, request, backend):
            return LocalLaceIntentProof(
                run_id="other-run",
                step_id="step-1",
                command_ordinal=1,
                run_seq=2,
                attempt_identity="sha256:" + "a" * 64,
            )

    request.local_resource_intent = FakeReservation()
    with pytest.raises(FleetPlanInvalid, match="did not return exact committed intent"):
        _execute(backend, request)
    touched.assert_not_called()
