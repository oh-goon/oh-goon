"""EXEC-01B: actual standard FleetRun worker and Game Tape with explicit host consent.

External Hashmarks dependency is NOT available in hosted CI. For these tests ONLY,
replace its leaf identity producer. Fleet admission, repository execution, subprocess,
SQLite Journal, Enforcer lifecycle and Game Tape remain production implementations.
A real installed-Hashmarks host qualification is still required and never inferred.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
from oh_goon.integrations.hashmarks import identity as external_identity
from oh_goon.services.fleet_plans import prepare_execution_target
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "standard-atomic"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/WSL host process tests")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, script: str | None = None):
    repo = tmp_path / "repos" / "sample"
    repo.parent.mkdir()
    shutil.copytree(EXAMPLE, repo)
    if script is not None:
        (repo / "scripts" / "hello.sh").write_text(script, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.org")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "database.db"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return repo


@pytest.fixture
def simulated_external_identity(monkeypatch: pytest.MonkeyPatch):
    # NOT a Hashmarks substitute in production; deterministic external adapter
    # at the one published leaf boundary while exercising the real Fleet code.
    def supplied_identity(path: Path, **_kwargs):
        files = (sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts)
                 if path.is_dir() else [path])
        payload = b"".join(p.relative_to(path).as_posix().encode()+p.read_bytes()
                           for p in files) if path.is_dir() else path.read_bytes()
        return external_identity.HashmarksTreeIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload), entry_count=len(files),
        )
    monkeypatch.setattr(external_identity, "_identify_one", supplied_identity)


def _run(repo: Path, *, run_id: str):
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent)
        target = runtime.plans.prepare_execution_target(repo / "goon.yml", lock_path=repo / "goon.lock")
        admitted = runtime.runs.start_prepared_plan(
            target, repository_ids={repository_id(repo)}, fleet_run_id=run_id,
        )
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=20)
        # Must be a persisted original-Journal readback, not a transient worker return.
        persisted = runtime.runs.get(admitted.fleet_run_id)
        assert persisted.run == detail.run
        assert persisted.game_tape.status == detail.game_tape.status
        return detail, persisted
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_durable_standard_fleet_run_executes_real_atomic_and_publishes_tape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, simulated_external_identity
):
    repo = _repo(tmp_path, monkeypatch)
    detail, persisted = _run(repo, run_id="exec01b-pass")
    assert detail.outcome.value == "SUCCESS"
    assert detail.run.status.value == "COMPLETED"
    assert len(detail.operations) == 1
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 0
    assert step.step_id == "hello"
    assert step.stdout_evidence.sha256 == hashlib.sha256(b"oh-goon-atomic-ok").hexdigest()
    assert persisted.game_tape.status.value == "PASSED"


def test_durable_standard_fleet_run_records_nonzero_real_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, simulated_external_identity
):
    repo = _repo(tmp_path, monkeypatch, script="#!/bin/sh\necho real-failure\nexit 17\n")
    detail, persisted = _run(repo, run_id="exec01b-fail")
    assert detail.outcome.value != "SUCCESS"
    assert len(detail.operations) == 1
    step = detail.operations[0].evidence.plan.steps[0]
    assert step.returncode == 17
    assert persisted.game_tape.status.value != "PASSED"


def test_standard_local_refuses_cicd_before_fleet_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from oh_goon.core.models.target import FleetPlanTarget
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.execution_identity import ExecutionIdentity
    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
    repo = _repo(tmp_path, monkeypatch)
    strict = ResolvedFleetPlan.create(
        name="strict", description="", execution=CicdExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="exec", argv=("sh", "scripts/hello.sh")),),
    )
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        # Directly exercise the canonical admission authority before planning.
        original = runtime.plans.prepare_execution_target(repo / "goon.yml", lock_path=repo / "goon.lock")
        changed = original.model_copy(update={
            "plan": strict, "identity": ExecutionIdentity(digest=build_execution_lock(strict).digest),
        })
        with pytest.raises(FleetPlanInvalid, match="refuses CI/CD"):
            runtime.runs._lifecycle.require_fleet_prework_available(changed, "run-strict")
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_repository_subject_identity_ignores_normal_git_index_refresh(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    original = repository_subject_instance_id(repo)
    marker = repo / ".git" / "index.lock"
    marker.write_bytes(b"transient")
    marker.unlink()
    assert repository_subject_instance_id(repo) == original


def test_repository_subject_identity_detects_replaced_git_marker(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = repo / ".git"
    marker.mkdir()
    before = repository_subject_instance_id(repo)
    marker.rename(repo / "previous-marker")
    marker.mkdir()
    assert repository_subject_instance_id(repo) != before


def test_default_runtime_still_requires_authenticated_ingress(tmp_path: Path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    runtime = ApplicationRuntime.build()
    try:
        with pytest.raises(Exception, match="not provisioned"):
            runtime.plans.require_cli_plan_preparation(None)
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_missing_hashmarks_is_actionable_admission_error(tmp_path: Path, monkeypatch):
    from oh_goon.integrations.hashmarks import identity
    repo = _repo(tmp_path, monkeypatch)
    def missing(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'hashmarks'")
    monkeypatch.setattr(identity, "_identify_one", missing)
    with pytest.raises(FleetPlanInvalid, match="pinned Hashmarks runtime dependency"):
        identity.identify_path(repo)


def test_cli_explicit_standard_local_wait_runs_and_prints_terminal_tape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, simulated_external_identity, capsys
):
    from oh_goon.cli import main
    import json
    repo = _repo(tmp_path, monkeypatch)
    registration = ApplicationRuntime.build(standard_local=True)
    try:
        registration.locations.add(repo.parent)
    finally:
        registration.shutdown(wait_for_runs=True)
    result = main([
        "plan", "run", str(repo / "goon.yml"),
        "--repository-id", repository_id(repo),
        "--standard-local", "--wait", "--json",
    ])
    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out.strip())
    assert payload["status"] == "COMPLETED"
    assert payload["outcome"] == "SUCCESS"
    check = ApplicationRuntime.build(standard_local=True)
    try:
        assert check.runs.get(payload["fleet_run_id"]).game_tape.status.value == "PASSED"
    finally:
        check.shutdown(wait_for_runs=True)


def test_standard_local_cli_requires_explicit_wait_and_default_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from oh_goon.cli import main
    repo = _repo(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as failed:
        main(["plan", "run", str(repo / "goon.yml"), "--all-repositories", "--standard-local"])
    assert failed.value.code == 2


def test_durable_standard_fleet_cancel_owned_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, simulated_external_identity
):
    repo = _repo(tmp_path, monkeypatch, script="#!/bin/sh\nsleep 30\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent)
        target = runtime.plans.prepare_execution_target(repo / "goon.yml", lock_path=repo / "goon.lock")
        admitted = runtime.runs.start_prepared_plan(
            target, repository_ids={repository_id(repo)}, fleet_run_id="exec01b-cancel",
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = runtime.runs.get(admitted.fleet_run_id).run.status.value
            if state == "RUNNING":
                break
            time.sleep(0.03)
        assert state == "RUNNING"
        runtime.runs.cancel(admitted.fleet_run_id)
        result = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=15)
        assert result.run.status.value in {"CANCELLED", "COMPLETED"}
        assert result.outcome.value != "SUCCESS"
    finally:
        runtime.shutdown(wait_for_runs=True)
