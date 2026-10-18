"""AGENT-01B2B: original standard Fleet admission and durable Agent Game Tape."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.hashmarks import identity as external_identity
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

    # External Hashmarks producer is not installed on this hosted test machine.
    # Replace only its leaf adapter; all Fleet admission, execution and Game Tape are real.
    def supply_hashmarks(path: Path, **_kwargs):
        if path.is_file():
            payload = path.read_bytes()
            return external_identity.HashmarksTreeIdentity(
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                entry_count=1,
            )
        files = sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts)
        payload = b"".join(p.relative_to(path).as_posix().encode() + p.read_bytes() for p in files)
        return external_identity.HashmarksTreeIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload),
            entry_count=len(files),
        )

    monkeypatch.setattr(external_identity, "_identify_one", supply_hashmarks)
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
    frozen, handoff, _ = resolver.resolve_frozen_local_search_agent(
        plan,
        inputs=opts["inputs"],
        repository_root=opts["agent_repository_root"],
        skill_root=opts["agent_skill_root"],
        allowed_acceptance=opts["allowed_agent_acceptance"],
    )
    write_execution_lock(plan.parent / "goon.lock", build_execution_lock(frozen))
    return frozen, handoff


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
        assert persisted.operations[0].evidence.plan.steps[0].returncode == 1
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
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=frozen,
        source=PlanFileSource(sha256="a" * 64),
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
    import threading

    import oh_goon.integrations.fleet_plan_step_execution as steps

    plan, repo, opts = _case(tmp_path, monkeypatch)
    _lock(plan, opts)
    entered, release = threading.Event(), threading.Event()
    real_step = steps._execute_command_step

    def original_supervised_launch(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=12), "test cancellation handshake timed out"
        return real_step(*args, **kwargs)

    monkeypatch.setattr(steps, "_execute_command_step", original_supervised_launch)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-cancel", **opts
        )
        assert entered.wait(timeout=15), "Agent attempt did not reach original Step supervisor"
        runtime.runs.cancel(started.fleet_run_id)
        release.set()
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert detail.outcome.value != "SUCCESS"
        assert persisted.game_tape.status.value != "PASSED"
    finally:
        release.set()
        runtime.shutdown(wait_for_runs=True)


def test_original_agent_fleet_process_failure_is_durably_recorded(tmp_path, monkeypatch):
    plan, repo, opts = _case(tmp_path, monkeypatch)
    _lock(plan, opts)
    # Fault-inject only the installed command implementation. The canonical
    # original Fleet Step supervisor, journal and Tape are still exercised.
    failing_tools = tmp_path / "failing-tools"
    failing_tools.mkdir()
    rg = failing_tools / "rg"
    rg.write_text("#!/bin/sh\necho injected-failing-rg >&2\nexit 17\n")
    rg.chmod(0o755)
    monkeypatch.setenv("PATH", str(failing_tools) + os.pathsep + os.environ["PATH"])
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent.parent)
        started = runtime.plans.run(
            plan, repository_ids={repository_id(repo)}, fleet_run_id="agent01b2b-failure", **opts
        )
        terminal = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        persisted = runtime.runs.get(started.fleet_run_id)
        assert terminal.outcome.value != "SUCCESS"
        assert persisted.game_tape.status.value != "PASSED"
        failure = persisted.operations[0].evidence.plan.steps[0]
        assert failure.kind == "agent"
        assert failure.returncode == 17
    finally:
        runtime.shutdown(wait_for_runs=True)
