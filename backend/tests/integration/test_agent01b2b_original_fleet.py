"""AGENT-01B2B: original standard Fleet admission and durable Agent Game Tape."""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    ProcessExitedEvent,
    ProcessStartedEvent,
    StepAttemptCreatedEvent,
)
from oh_goon.core.process_birth import observe_process_birth, observe_process_state
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime import goon_templates
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("rg") is None, reason="Linux/WSL ripgrep required"
)


def _case(tmp_path: Path, monkeypatch, *, task="search:needle", effects=""):
    repository = tmp_path / "repositories" / "team" / "app"
    repository.mkdir(parents=True)
    (repository / "code.txt").write_text("needle from real original Fleet\n")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)
    skill = tmp_path / "skills" / "team" / "search"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Offline read-only skill\n")
    goon = tmp_path / "goons" / "agent" / "search"
    goon.mkdir(parents=True)
    (goon / "README.md").write_text("Offline search\n")
    (goon / "goon.yml").write_text(
        '''inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
  - uses: agent
    harness: local-search
    task: "'''
        + task
        + '"\n'
        + effects
    )
    plan = tmp_path / "plan.yml"
    plan.write_text("""inputs: [repo, skill, acceptance]
execution: {purpose: verification}
goons: [agent/search]
""")
    monkeypatch.setattr(goon_templates, "default_goon_templates_root", lambda: tmp_path / "goons")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "database.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    opts = dict(
        inputs={"repo": "local:team/app", "skill": "local:team/search", "acceptance": "review"},
        agent_repository_root=tmp_path / "repositories",
        agent_skill_root=tmp_path / "skills",
        allowed_agent_acceptance=("review",),
    )
    return plan, repository, opts


def _lock(plan: Path, opts):
    from oh_goon.runtime.goon_templates import GoonTemplateStore

    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    frozen, handoff, _ = resolver.resolve_frozen_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    write_execution_lock(plan.parent / "goon.lock", build_execution_lock(frozen))
    return frozen, handoff


@contextmanager
def _stopped_agent_process(
    runtime: ApplicationRuntime, run_id: str, step_id: str, *, ordinal: int = 1
) -> Generator[tuple[ProcessStartedEvent, int], None, None]:
    """Stop only a live native process bound to the original Journal attempt."""
    deadline = monotonic() + 12
    while True:
        events = runtime.runs.events(run_id).events
        attempts = [
            event
            for event in events
            if isinstance(event, StepAttemptCreatedEvent) and event.step_id == step_id
        ]
        assert len(attempts) <= 1, "test cut requires one original Step attempt"
        started = [
            event
            for event in events
            if type(event) is ProcessStartedEvent
            and event.step_id == step_id
            and attempts
            and event.step_attempt_id == attempts[0].step_attempt_id
        ]
        if len(started) >= ordinal:
            target = started[ordinal - 1]
            break
        if monotonic() >= deadline:
            pytest.fail("native process cut not reached in the original Journal")
        Event().wait(0.001)

    try:
        descriptor = os.pidfd_open(target.pid)
    except ProcessLookupError:
        pytest.fail("native process cut not reached: selected process already exited")
    stop_requested = False
    try:
        birth = observe_process_birth(target.pid)
        assert birth is not None, "native process cut not reached: birth unavailable"
        assert target.process_identity == f"{birth.boot_id}:{birth.start_ticks}", (
            "native process cut not reached: original process identity changed"
        )
        assert not select.select([descriptor], [], [], 0)[0], (
            "native process cut not reached: selected process already exited"
        )
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
        except ProcessLookupError:
            pytest.fail("native process cut not reached: selected process exited before stop")
        stop_requested = True
        deadline = monotonic() + 3
        while observe_process_state(target.pid) != "T":
            assert not select.select([descriptor], [], [], 0)[0], (
                "native process cut not reached: selected process exited before stop"
            )
            assert monotonic() < deadline, "native process cut not reached: stop unacknowledged"
            Event().wait(0.001)
        assert observe_process_birth(target.pid) == birth
        yield target, descriptor
    finally:
        try:
            if stop_requested and not select.select([descriptor], [], [], 0)[0]:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGCONT)
                except ProcessLookupError:
                    pass
        finally:
            os.close(descriptor)


def test_original_fleet_admits_real_read_only_agent_and_persists_tape(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    frozen, handoff = _lock(plan, opts)
    assert len(frozen.steps) == 1 and frozen.steps[0].kind == "agent"
    assert frozen.steps[0].step_id.endswith("." + handoff.step_id)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-pass", **opts
        )
        terminal = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert persisted.run == terminal.run
        assert persisted.game_tape.status.value == "PASSED"
        assert persisted.outcome.value == "SUCCESS"
        step = persisted.operations[0].evidence.plan.steps[0]
        assert step.step_id == frozen.steps[0].step_id
        assert step.returncode == 0
        assert step.kind == "agent"
        assert b"needle" not in json.dumps(started.target.model_dump(mode="json")).encode()
        assert step.stdout_evidence.byte_count > 0
        assert step.stdout_evidence.line_count >= 1
    finally:
        runtime.shutdown(wait_for_runs=True)
    # Independently reopen the same SQLite journal; successful observations
    # must not depend on a live worker object or transient in-memory state.
    reopened_runtime = ApplicationRuntime.build(standard_local=True)
    try:
        reopened = reopened_runtime.runs.get("agent01b2b-pass")
        assert reopened.game_tape.status.value == "PASSED"
        assert reopened.operations[0].evidence.plan.steps[0].kind == "agent"
    finally:
        reopened_runtime.shutdown(wait_for_runs=True)


def test_agent_source_absent_lock_refuses_before_fleet(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        with pytest.raises(FleetPlanInvalid):
            runtime.plans.run(plan, repository_ids={repository_id(repo)}, **opts)
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_standard_local_agent_cannot_execute_unrequested_write(tmp_path, monkeypatch):
    plan, repo, opts = _case(
        tmp_path, monkeypatch, effects="    requested_effects:\n      repository_access: write\n"
    )
    with pytest.raises(FleetPlanInvalid, match="read-only"):
        _lock(plan, opts)


def test_no_match_is_durable_success_through_original_fleet(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch, task="search:absent-string")
    _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-no-match", **opts
        )
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert detail.outcome.value == "SUCCESS"
        assert persisted.game_tape.status.value == "PASSED"
        assert persisted.operations[0].evidence.plan.steps[0].returncode == 0
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_live_source_drift_refuses_stale_lock_before_fleet(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    _lock(plan, opts)
    # The lock binds the exact frozen repository and Skill bytes. Requiring a
    # new lock is preferable to silently switching the authorized source.
    (repo / "code.txt").write_text("live checkout no longer has the query\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        with pytest.raises(FleetPlanInvalid, match="stale"):
            runtime.plans.run(plan, repository_ids={repository_id(repo)}, **opts)
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize("change", ["owner", "task", "acceptance", "missing_standard_mode"])
def test_agent_ingress_fails_closed_before_work(tmp_path, monkeypatch, change):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    _lock(plan, opts)
    if change == "owner":
        wrong = tmp_path / "repositories" / "other"
        wrong.mkdir()
        monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "database.sqlite"))
        selected_repos = {repository_id(wrong)}
    else:
        selected_repos = {repository_id(repo)}
    if change == "task":
        goon_path = tmp_path / "goons" / "agent" / "search" / "goon.yml"
        goon_path.write_text(goon_path.read_text().replace("search:needle", "search:other"))
    if change == "acceptance":
        opts["allowed_agent_acceptance"] = ("other",)
    runtime = ApplicationRuntime.build(standard_local=(change != "missing_standard_mode"))
    try:
        runtime.locations.add(repo.parent.parent)
        if change == "missing_standard_mode":
            from oh_goon.core.enforcer_lace_trust import TrustProtocolError

            expected = TrustProtocolError
        else:
            expected = FleetPlanInvalid
        with pytest.raises(expected):
            runtime.plans.run(plan, repository_ids=selected_repos, **opts)
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_agent_delivery_reference_must_match_resolved_identity(tmp_path, monkeypatch):
    from pydantic import ValidationError

    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.goon_plan_source import PlanFileSource
    from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget

    plan, _, opts = _case(tmp_path, monkeypatch)
    frozen, _ = _lock(plan, opts)
    step = frozen.steps[0]
    assert frozen.configuration is not None
    assert frozen.configuration.root_source_sha256 is not None
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=frozen,
        source=PlanFileSource(sha256=frozen.configuration.root_source_sha256),
        identity=ExecutionIdentity(digest=build_execution_lock(frozen).digest),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                handoff_identity=step.handoff_identity,
                delivery_sha256=step.delivery_sha256,
                reference_id="file-opaque",
            ),
        ),
    )
    assert (
        FleetPlanTarget.model_validate_json(target.model_dump_json()).agent_deliveries
        == target.agent_deliveries
    )
    with pytest.raises(ValidationError):
        FleetPlanTarget.model_validate({**target.model_dump(), "agent_deliveries": []})
    bad = {**target.agent_deliveries[0].model_dump(), "delivery_sha256": "0" * 64}
    with pytest.raises(ValidationError):
        FleetPlanTarget.model_validate({**target.model_dump(), "agent_deliveries": [bad]})


def test_original_agent_fleet_cancel_is_durably_recorded(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    frozen, _ = _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-cancel", **opts
        )
        with _stopped_agent_process(runtime, started.fleet_run_id, frozen.steps[0].step_id) as (
            target,
            descriptor,
        ):
            runtime.runs.cancel(started.fleet_run_id)
            signal.pidfd_send_signal(descriptor, signal.SIGCONT)
            detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert detail.outcome.value != "SUCCESS"
        assert persisted.game_tape.status.value != "PASSED"
        assert persisted.run.status.value == "CANCELLED"
        assert any(
            type(event) is DescendantsQuiescentEvent
            and event.physical_execution_id == target.physical_execution_id
            for event in persisted.lifecycle_events
        )
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_original_agent_fleet_process_failure_is_durably_recorded(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    frozen, _ = _lock(plan, opts)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-failure", **opts
        )
        with _stopped_agent_process(runtime, started.fleet_run_id, frozen.steps[0].step_id) as (
            target,
            descriptor,
        ):
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            terminal = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert terminal.outcome.value != "SUCCESS"
        assert persisted.game_tape.status.value != "PASSED"
        evidence = persisted.operations[0].evidence
        assert isinstance(evidence, FleetPlanOperationEvidence)
        failure = evidence.plan.steps[0]
        assert isinstance(failure, FailedPlanStepExecution)
        assert failure.kind == "agent"
        assert failure.returncode == -signal.SIGKILL
        assert failure.settlement.outcome == "FAILED"
        assert failure.settlement.cleanup == "VERIFIED"
        assert any(
            type(event) is ProcessExitedEvent
            and event.physical_execution_id == target.physical_execution_id
            and event.returncode == -signal.SIGKILL
            for event in persisted.lifecycle_events
        )
    finally:
        runtime.shutdown(wait_for_runs=True)
