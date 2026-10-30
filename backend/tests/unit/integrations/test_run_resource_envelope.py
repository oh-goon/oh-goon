from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import _fleet_plan_cgroup_resources as native_cgroups
from oh_goon.integrations import run_resource_envelope as envelope

_DIGEST = "sha256:" + "d" * 64
_INVOCATION = "e" * 32
_BIRTH = ProcessBirthIdentity(pid=os.getpid(), boot_id="boot", start_ticks=3)


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> envelope.ResolvedRunResourceScope:
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/captured/runtime")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/captured/bus")
    monkeypatch.setattr(
        native_cgroups,
        "standard_resource_scope_command",
        lambda policy, executable, **_kwargs: (
            executable,
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            "--slice=app.slice",
            "--property=CPUQuota=100%",
            "--",
        ),
    )
    result = envelope.prepare_run_resource_scope("fleet-run", CicdResourcePolicy(cpu_millis=1000))
    return result


def _properties(scope: envelope.ResolvedRunResourceScope, **changes: str) -> dict[str, str]:
    return {
        "Id": scope.unit_name,
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "InvocationID": _INVOCATION,
        "ControlGroup": "/" + scope.unit_name,
        "Transient": "yes",
        "CollectMode": "inactive",
        "Description": "oh-goon-run-handoff-" + _DIGEST,
        "Result": "success",
        "CPUUsageNSec": "1234000",
        "MemoryPeak": "6789",
        "OOMKills": "0",
        "OOMPolicy": "kill",
        **changes,
    }


def _started(scope: envelope.ResolvedRunResourceScope) -> RunResourceEnvelopeEvidence:
    return RunResourceEnvelopeEvidence(
        fleet_run_id=scope.fleet_run_id,
        unit_name=scope.unit_name,
        invocation_id=_INVOCATION,
        handoff_sha256=_DIGEST,
        process_birth=_BIRTH,
        resources=scope.resources,
        cgroup_device=1,
        cgroup_inode=2,
        cpu_max="100000 100000",
        memory_max_bytes=None,
        memory_swap_max_bytes=None,
        pids_max=None,
    )


def test_selected_scope_transports_bytes_environment_and_literal_command(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    restored = envelope.ResolvedRunResourceScope.from_payload(scope.to_payload())
    assert restored == scope
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/changed/ambient")
    assert restored.environment["XDG_RUNTIME_DIR"] == "/captured/runtime"
    command = restored.command(_DIGEST, (sys.executable, "-m", "oh_goon.cli", "$literal"))
    assert "--collect" not in command
    assert f"--unit={scope.unit_name}" in command
    assert "--property=CollectMode=inactive" in command
    assert command[-5:] == ("--", sys.executable, "-m", "oh_goon.cli", "$literal")
    assert "--description=oh-goon-run-handoff-" + _DIGEST in command
    assert "--expand-environment=no" in command


@pytest.mark.parametrize(
    "change", ("extra", "environment", "schema", "unit", "digest", "empty", "fleet-id")
)
def test_malformed_transport_is_refused(
    scope: envelope.ResolvedRunResourceScope, change: str
) -> None:
    payload = scope.to_payload()
    if change == "extra":
        payload["extra"] = True
    elif change == "environment":
        payload["manager_environment"] = [[["unhashable"], "value"]]
    elif change == "schema":
        payload["schema"] = "other"
    elif change == "unit":
        payload["unit_name"] = "other.scope"
    elif change == "empty":
        payload["resources"] = {}
    elif change == "fleet-id":
        payload["fleet_run_id"] = "x" * 161
    else:
        payload["systemctl_sha256"] = "unknown"
    with pytest.raises(FleetPlanInvalid):
        envelope.ResolvedRunResourceScope.from_payload(payload)


def test_empty_policy_never_resolves_or_observes_a_native_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(_name: str) -> None:
        raise AssertionError("empty policy must not acquire native state")

    monkeypatch.setattr(shutil, "which", unexpected)
    with pytest.raises(FleetPlanInvalid, match="at least one"):
        envelope.prepare_run_resource_scope("fleet-run", CicdResourcePolicy())


def test_original_guard_refusal_precedes_scope_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)

    def refused(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise FleetPlanInvalid("finite private cpu.max")

    monkeypatch.setattr(native_cgroups, "standard_resource_scope_command", refused)
    with pytest.raises(FleetPlanInvalid, match="finite private"):
        envelope.prepare_run_resource_scope("fleet-run", CicdResourcePolicy(cpu_millis=1000))


@pytest.fixture
def child(
    scope: envelope.ResolvedRunResourceScope, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], Path]:
    target = tmp_path / scope.unit_name
    target.mkdir()
    for name, value in {
        "cpu.max": "100000 100000",
        "memory.max": "max",
        "memory.swap.max": "max",
        "pids.max": "max",
        "cgroup.type": "domain",
        "cgroup.procs": str(os.getpid()),
    }.items():
        (target / name).write_text(value)
    properties = _properties(scope)
    monkeypatch.setattr(envelope, "observe_process_birth", lambda _pid=None: _BIRTH)
    monkeypatch.setattr(envelope, "_query", lambda _scope: properties)
    monkeypatch.setattr(native_cgroups, "_standard_mountinfo_snapshot", lambda: ("snapshot",))
    monkeypatch.setattr(native_cgroups, "_standard_cgroup_directory", lambda *_args, **_kw: target)
    monkeypatch.setattr(native_cgroups, "_standard_cgroup_path", lambda *_args, **_kw: target)
    return properties, target


def test_actual_child_controls_and_birth_are_retained_before_work(
    scope: envelope.ResolvedRunResourceScope, child: tuple[dict[str, str], Path]
) -> None:
    evidence = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    assert evidence.process_birth == _BIRTH
    assert evidence.invocation_id == _INVOCATION and evidence.handoff_sha256 == _DIGEST
    assert evidence.cpu_max == "100000 100000"
    assert evidence.cgroup_inode == child[1].stat().st_ino


@pytest.mark.parametrize("boundary", ("digest", "invocation", "pid", "domain", "cpu", "birth"))
def test_child_refuses_unproven_actual_context(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    properties, target = child
    if boundary == "digest":
        properties["Description"] = "oh-goon-run-handoff-sha256:" + "f" * 64
    elif boundary == "invocation":
        properties["InvocationID"] = "0" * 32
    elif boundary == "pid":
        (target / "cgroup.procs").write_text("1")
    elif boundary == "domain":
        (target / "cgroup.type").write_text("threaded")
    elif boundary == "cpu":
        (target / "cpu.max").write_text("max 100000")
    else:
        calls = iter((_BIRTH, replace(_BIRTH, start_ticks=4)))
        monkeypatch.setattr(envelope, "observe_process_birth", lambda: next(calls))
    with pytest.raises(FleetPlanInvalid):
        envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)


def test_native_query_uses_selected_tool_and_captured_manager_only(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def query(*args: str, **kwargs: object) -> CommandResult:
        captured.append((args, kwargs))
        return CommandResult(0, "Id=" + scope.unit_name + "\n", "")

    monkeypatch.setattr(envelope, "run_command", query)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "changed")
    assert envelope._query(scope) == {"Id": scope.unit_name}
    assert captured[0][0][0] == scope.systemctl
    assert captured[0][1]["env"] == scope.environment


@pytest.mark.parametrize("reason", ("missing", "birth", "digest", "invocation"))
def test_finish_never_mutates_an_unbound_unit(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    started = _started(scope)
    properties = _properties(scope, ActiveState="failed", Result="oom-kill")
    if reason == "digest":
        properties["Description"] = "other"
    elif reason == "invocation":
        properties["InvocationID"] = "f" * 32
    monkeypatch.setattr(envelope, "_query", lambda _scope: properties)

    def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("unbound native unit must not be mutated")

    monkeypatch.setattr(envelope, "run_command", unexpected)
    observed = envelope.finish_run_resource_scope(
        scope,
        None if reason == "missing" else started,
        expected_birth=None
        if reason == "missing"
        else replace(_BIRTH, start_ticks=4)
        if reason == "birth"
        else _BIRTH,
        handoff_sha256=_DIGEST,
    )
    assert observed.state == "INCOMPLETE" and observed.cleanup == "INCOMPLETE"


def test_failed_native_invocation_is_observed_before_its_state_is_released(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties = _properties(scope, ActiveState="failed", Result="oom-kill", OOMKills="2")
    effects: list[tuple[str, ...]] = []
    monkeypatch.setattr(envelope, "_query", lambda _scope: properties)

    def reset(*args: str, **_kwargs: object) -> CommandResult:
        effects.append(args)
        return CommandResult(0, "", "")

    monkeypatch.setattr(envelope, "run_command", reset)
    observed = envelope.finish_run_resource_scope(
        scope,
        _started(scope),
        expected_birth=_BIRTH,
        handoff_sha256=_DIGEST,
    )
    assert observed.invocation_id == _INVOCATION
    assert observed.result == "oom-kill" and observed.oom_kill_count == 2
    assert observed.cpu_usage_usec == 1234 and observed.memory_peak_bytes == 6789
    assert observed.pids_peak is None and observed.state == "TERMINAL"
    assert observed.cleanup == "VERIFIED"
    assert len(effects) == 1 and effects[0][-2:] == ("reset-failed", scope.unit_name)


def test_unrealized_or_absent_native_metrics_remain_unknown(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties = _properties(
        scope,
        ActiveState="inactive",
        CPUUsageNSec=str((1 << 64) - 1),
        MemoryPeak="",
        OOMKills="unavailable",
    )
    monkeypatch.setattr(envelope, "_query", lambda _scope: properties)
    observed = envelope.finish_run_resource_scope(
        scope, _started(scope), expected_birth=_BIRTH, handoff_sha256=_DIGEST
    )
    assert observed.cpu_usage_usec is None and observed.memory_peak_bytes is None
    assert observed.oom_kill_count is None and observed.state == "TERMINAL"
    assert observed.cleanup == "VERIFIED"


def test_failed_unbound_unit_is_never_reset_even_with_matching_native_description(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        envelope,
        "_query",
        lambda _scope: _properties(scope, ActiveState="failed", Result="oom-kill"),
    )

    def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("a discovered invocation is not prior unit ownership")

    monkeypatch.setattr(envelope, "run_command", unexpected)
    observed = envelope.finish_run_resource_scope(
        scope, None, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    )
    assert observed.invocation_id is None and observed.oom_kill_count is None
    assert observed.cleanup == "INCOMPLETE" and observed.state == "INCOMPLETE"


@pytest.mark.parametrize("boundary", ("verified", "policy", "group", "memory", "pids", "swap"))
def test_memory_envelope_requires_actual_group_failure_and_requested_limits(
    scope: envelope.ResolvedRunResourceScope, child: tuple[dict[str, str], Path], boundary: str
) -> None:
    properties, target = child
    selected = replace(scope, resources=CicdResourcePolicy(memory_mib=64, max_processes=16))
    properties["OOMPolicy"] = "continue" if boundary == "policy" else "kill"
    (target / "memory.oom.group").write_text("0" if boundary == "group" else "1")
    (target / "memory.max").write_text(str((128 if boundary == "memory" else 64) * 1024 * 1024))
    (target / "memory.swap.max").write_text("1" if boundary == "swap" else "0")
    (target / "pids.max").write_text("32" if boundary == "pids" else "16")
    if boundary != "verified":
        with pytest.raises(FleetPlanInvalid):
            envelope.verify_current_run_resource_scope(selected, handoff_sha256=_DIGEST)
    else:
        observed = envelope.verify_current_run_resource_scope(selected, handoff_sha256=_DIGEST)
        assert observed.oom_policy == "kill" and observed.memory_oom_group is True


def test_memory_selection_requests_supported_native_group_failure_policy(
    scope: envelope.ResolvedRunResourceScope,
) -> None:
    selected = envelope.prepare_run_resource_scope("fleet-run", CicdResourcePolicy(memory_mib=64))
    assert "--property=OOMPolicy=kill" in selected.command_prefix


def test_cleanup_failure_preserves_already_observed_native_result_and_metrics(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        envelope,
        "_query",
        lambda _scope: _properties(scope, ActiveState="failed", Result="oom-kill", OOMKills="1"),
    )

    def unavailable(*_args: object, **_kwargs: object) -> CommandResult:
        raise OSError("manager disappeared")

    monkeypatch.setattr(envelope, "run_command", unavailable)
    observed = envelope.finish_run_resource_scope(
        scope, _started(scope), expected_birth=_BIRTH, handoff_sha256=_DIGEST
    )
    assert observed.result == "oom-kill" and observed.oom_kill_count == 1
    assert observed.cleanup == "INCOMPLETE"


def test_changed_selected_tool_bytes_refuse_before_native_query(
    scope: envelope.ResolvedRunResourceScope, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = replace(scope, systemctl_sha256="0" * 64)

    def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("changed selected tool must not execute")

    monkeypatch.setattr(envelope, "run_command", unexpected)
    with pytest.raises(FleetPlanInvalid, match="bytes changed"):
        envelope._query(changed)


def test_active_cleanup_stops_only_the_bound_group_and_preserves_final_result(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    properties, _target = child
    effects: list[tuple[str, ...]] = []

    def mutate(*args: str, **kwargs: object) -> CommandResult:
        effects.append(args)
        assert kwargs["env"] == scope.environment
        if args[-2] == "stop":
            properties.update(ActiveState="failed", Result="timeout")
        return CommandResult(0, "", "")

    monkeypatch.setattr(envelope, "run_command", mutate)
    observed = envelope.finish_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    )
    assert [args[-2] for args in effects] == ["stop", "reset-failed"]
    assert all(args[-1] == scope.unit_name for args in effects)
    assert observed.result == "timeout" and observed.cleanup == "VERIFIED"


def test_replaced_active_group_is_not_stopped(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    _properties, target = child
    target.rename(target.with_suffix(".old"))
    target.mkdir()

    def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("replaced physical group must not be stopped")

    monkeypatch.setattr(envelope, "run_command", unexpected)
    observed = envelope.finish_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    )
    assert observed.cleanup == "INCOMPLETE"


def _retired_properties(scope: envelope.ResolvedRunResourceScope) -> dict[str, str]:
    # systemctl259 returns an inactive not-found stub, including a default Result=success.
    # That default must not become the original invocation's result or accounting.
    return {
        "Id": scope.unit_name,
        "LoadState": "not-found",
        "ActiveState": "inactive",
        "InvocationID": "",
        "ControlGroup": "",
        "Transient": "no",
        "Result": "success",
    }


def _remove_group(directory: Path) -> None:
    for control in directory.iterdir():
        control.unlink()
    directory.rmdir()


def test_parent_pin_retains_readonly_original_object_and_closes_deterministically(
    scope: envelope.ResolvedRunResourceScope, child: tuple[dict[str, str], Path], tmp_path: Path
) -> None:
    import fcntl

    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    with envelope.pin_started_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    ) as pinned:
        descriptor = pinned.fileno()
        assert os.get_inheritable(descriptor) is False
        assert fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert os.fstat(descriptor).st_ino == started.cgroup_inode
    with pytest.raises(OSError):
        os.fstat(descriptor)
    with (tmp_path / "other").open("w") as other:
        pinned.close()
        assert os.fstat(other.fileno()).st_ino == (tmp_path / "other").stat().st_ino


@pytest.mark.parametrize("boundary", ("birth", "invocation", "pid", "domain", "limits"))
def test_parent_pin_refuses_changed_waiting_child_before_ready_ack(
    scope: envelope.ResolvedRunResourceScope, child: tuple[dict[str, str], Path], boundary: str
) -> None:
    properties, target = child
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    expected = _BIRTH
    if boundary == "birth":
        expected = replace(_BIRTH, start_ticks=4)
    elif boundary == "invocation":
        properties["InvocationID"] = "f" * 32
    elif boundary == "pid":
        (target / "cgroup.procs").write_text("1")
    elif boundary == "domain":
        (target / "cgroup.type").write_text("threaded")
    else:
        (target / "cpu.max").write_text("max 100000")
    with pytest.raises(FleetPlanInvalid):
        envelope.pin_started_run_resource_scope(
            scope, started, expected_birth=expected, handoff_sha256=_DIGEST
        )


def test_pinned_original_retirement_and_native_unit_retirement_prove_cleanup_only(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    properties, target = child
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    with envelope.pin_started_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    ) as pinned:
        _remove_group(target)
        assert os.fstat(pinned.fileno()).st_nlink == 0
        properties.clear()
        properties.update(_retired_properties(scope))

        def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
            raise AssertionError("retired native objects need no destructive cleanup")

        monkeypatch.setattr(envelope, "run_command", unexpected)
        observed = envelope.finish_run_resource_scope(
            scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST, pinned=pinned
        )
    assert observed.cleanup == "VERIFIED" and observed.state == "INCOMPLETE"
    assert observed.invocation_id == started.invocation_id
    assert observed.result is None and observed.active_state is None
    assert observed.cpu_usage_usec is None and observed.memory_peak_bytes is None
    assert observed.oom_kill_count is None


@pytest.mark.parametrize("boundary", ("no-pin", "linked", "closed", "different-invocation"))
def test_missing_native_accounting_does_not_prove_cleanup_without_both_retirements(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    properties, target = child
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    with envelope.pin_started_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST
    ) as pinned:
        if boundary != "linked":
            _remove_group(target)
        if boundary == "closed":
            pinned.close()
        properties.clear()
        if boundary == "different-invocation":
            properties.update(_properties(scope, ActiveState="failed", InvocationID="f" * 32))
        else:
            properties.update(_retired_properties(scope))

        def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
            raise AssertionError("uncertain native retirement never authorizes a mutation")

        monkeypatch.setattr(envelope, "run_command", unexpected)
        observed = envelope.finish_run_resource_scope(
            scope,
            started,
            expected_birth=_BIRTH,
            handoff_sha256=_DIGEST,
            pinned=None if boundary == "no-pin" else pinned,
        )
    assert observed.cleanup == "INCOMPLETE" and observed.state == "INCOMPLETE"


def test_native_facts_are_published_at_each_destructive_boundary(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    properties, _target = child
    order: list[str] = []

    def publish(observation: RunResourceEnvelopeObservation) -> None:
        assert observation.cleanup == "INCOMPLETE"
        if observation.state == "INCOMPLETE":
            assert observation.active_state == "active" and observation.result is None
            order.append("pre-stop")
        else:
            assert observation.result == "oom-kill" and observation.oom_kill_count == 1
            order.append("pre-reset")

    def mutate(*args: str, **_kwargs: object) -> CommandResult:
        order.append(args[-2])
        if args[-2] == "stop":
            properties.update(ActiveState="failed", Result="oom-kill", OOMKills="1")
        return CommandResult(0, "", "")

    monkeypatch.setattr(envelope, "run_command", mutate)
    observed = envelope.finish_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST, before_cleanup=publish
    )
    assert order == ["pre-stop", "stop", "pre-reset", "reset-failed"]
    assert observed.cleanup == "VERIFIED" and observed.result == "oom-kill"


@pytest.mark.parametrize("active_state", ("active", "failed"))
def test_unpublished_native_facts_are_not_destroyed_by_cleanup(
    scope: envelope.ResolvedRunResourceScope,
    child: tuple[dict[str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    active_state: str,
) -> None:
    started = envelope.verify_current_run_resource_scope(scope, handoff_sha256=_DIGEST)
    properties, _target = child
    properties.update(ActiveState=active_state, Result="oom-kill", OOMKills="1")

    def unavailable(_observation: RunResourceEnvelopeObservation) -> None:
        raise OSError("native evidence sink unavailable")

    def unexpected(*_args: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("unpublished native evidence must survive its native mutation")

    monkeypatch.setattr(envelope, "run_command", unexpected)
    observed = envelope.finish_run_resource_scope(
        scope, started, expected_birth=_BIRTH, handoff_sha256=_DIGEST, before_cleanup=unavailable
    )
    assert observed.cleanup == "INCOMPLETE"
    assert observed.reason_code == "NATIVE_EVIDENCE_PUBLICATION_FAILED"
    if active_state == "failed":
        assert observed.result == "oom-kill" and observed.oom_kill_count == 1


def test_native_description_binds_selection_only_tamper_of_the_complete_sealed_packet(
    scope: envelope.ResolvedRunResourceScope, child: tuple[dict[str, str], Path], tmp_path: Path
) -> None:
    import json

    from oh_goon.cli_run_resources import (
        ResolvedCliState,
        prepare_run_resource_handoff,
        restore_run_resource_handoff,
    )
    from oh_goon.core.models.run_configuration import RunConfiguration
    from oh_goon.engine.atomic_execution_bytes import (
        atomic_execution_bytes_supported,
        seal_execution_bytes,
    )
    from oh_goon.services.run_configuration import (
        encode_run_configuration_snapshot,
        load_run_configuration,
    )

    if not atomic_execution_bytes_supported():
        pytest.skip("native Linux sealed execution descriptor required")
    configuration = tmp_path / "run.yml"
    configuration.write_text(
        "version: 1\nsource: {kind: catalog-plan, name: python/test}\n"
        "target: {repository_id: actual-subject}\nresources: {cpu_millis: 1000}\n"
    )
    snapshot = load_run_configuration(configuration)
    selected = snapshot.selection.model_dump(mode="json")
    selected["source"] = {"kind": "catalog-plan", "name": "python/quality"}
    changed_snapshot = replace(snapshot, selection=RunConfiguration.model_validate(selected))
    assert (
        changed_snapshot.content == snapshot.content and changed_snapshot.sha256 == snapshot.sha256
    )
    assert changed_snapshot.selection != snapshot.selection
    state = ResolvedCliState(
        database=tmp_path / "state.sqlite",
        league_preferences=tmp_path / "league.yml",
        sqlite_journal_mode="DELETE",
    )
    properties, _target = child
    with prepare_run_resource_handoff(snapshot, state, scope) as prepared:
        header = json.loads(prepared.header)
        original_packet = json.loads(os.pread(prepared.descriptor, header["byte_count"], 0))
        changed_packet = dict(original_packet)
        changed_packet["configuration_snapshot"] = encode_run_configuration_snapshot(
            changed_snapshot
        ).decode()
        assert {key for key in changed_packet if changed_packet[key] != original_packet[key]} == {
            "configuration_snapshot"
        }
        changed_source = tmp_path / "changed-packet.json"
        changed_source.write_text(json.dumps(changed_packet, separators=(",", ":")))
        changed_source.chmod(0o400)
        sealed = seal_execution_bytes(changed_source, label="selection-only changed handoff")
        try:
            changed_header = {
                **header,
                "descriptor": os.dup(sealed.descriptor),
                "identity": sealed.identity,
                "byte_count": sealed.byte_count,
                "mode": sealed.mode,
                "kernel_device": sealed.kernel_device,
                "kernel_inode": sealed.kernel_inode,
            }
            restored = restore_run_resource_handoff(json.dumps(changed_header))
            assert restored.snapshot == changed_snapshot
            assert restored.scope == scope and restored.sha256 == sealed.identity
            assert sealed.identity != prepared.sha256
            properties["Description"] = "oh-goon-run-handoff-" + prepared.sha256
            baseline = envelope.verify_current_run_resource_scope(
                scope, handoff_sha256=prepared.sha256
            )
            assert baseline.handoff_sha256 == prepared.sha256
            with pytest.raises(FleetPlanInvalid, match="handoff digest changed"):
                envelope.verify_current_run_resource_scope(scope, handoff_sha256=restored.sha256)
        finally:
            os.close(sealed.descriptor)
