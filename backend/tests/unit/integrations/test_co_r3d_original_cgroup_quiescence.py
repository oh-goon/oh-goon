"""CO-R3D: original attempt resource owner's negative physical-evidence cuts.

These are scoped cgroup observations, NOT independent Enforcer/LACE proof of
physical writer absence and NEVER permission for workspace/Git retirement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as original
from tests.factories.fleet_plan_executor import simulated_cgroup_custody  # noqa: F401

pytestmark = pytest.mark.usefixtures("simulated_cgroup_custody")


def _group(tmp_path: Path, *, population: str = "populated 0\n", procs: str = "") -> Path:
    group = tmp_path / "attempt-test"
    group.mkdir()
    (group / "cgroup.events").write_text(population, encoding="ascii")
    (group / "cgroup.procs").write_text(procs, encoding="ascii")
    return group


def test_real_kernel_style_empty_cgroup_requires_both_original_observations(tmp_path: Path) -> None:
    group = _group(tmp_path)
    identity = original._exact_cgroup_identity(group)
    original._wait_for_cgroup_empty(group, expected_identity=identity, timeout_seconds=0.02)
    (group / "cgroup.procs").unlink()
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        original._wait_for_cgroup_empty(group, expected_identity=identity, timeout_seconds=0.02)
    (group / "cgroup.procs").write_text("", encoding="ascii")
    (group / "cgroup.events").unlink()
    with pytest.raises(FleetPlanInvalid, match="observations unavailable"):
        original._wait_for_cgroup_empty(group, expected_identity=identity, timeout_seconds=0.02)


@pytest.mark.parametrize(
    "population,procs,expected",
    [
        ("populated 0\n", "123\n", "disagree"),
        ("populated 1\n", "", "remained populated"),
        ("populated 1\n", "123\n", "remained populated"),
        ("populated 0\npopulated 1\n", "", "ambiguous"),
        ("populated 2\n", "", "ambiguous"),
        ("populated 0\n", "abc\n", "malformed"),
    ],
)
def test_false_quiescence_inputs_fail_closed(
    tmp_path: Path,
    population: str,
    procs: str,
    expected: str,
) -> None:
    group = _group(tmp_path, population=population, procs=procs)
    with pytest.raises(FleetPlanInvalid, match=expected):
        original._wait_for_cgroup_empty(group, timeout_seconds=0.01)


def test_missing_replaced_and_symlink_cgroup_cannot_supply_original_identity(
    tmp_path: Path,
) -> None:
    group = _group(tmp_path)
    old = original._exact_cgroup_identity(group)
    renamed = tmp_path / "old"
    group.rename(renamed)
    with pytest.raises(FleetPlanInvalid, match="identity unavailable"):
        original._wait_for_cgroup_empty(group, expected_identity=old)
    group.symlink_to(renamed, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="symlink"):
        original._wait_for_cgroup_empty(group, expected_identity=old)
    group.unlink()
    group.mkdir()
    (group / "cgroup.events").write_text("populated 0\n", encoding="ascii")
    (group / "cgroup.procs").write_text("", encoding="ascii")
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        original._wait_for_cgroup_empty(group, expected_identity=old)


def test_original_owner_rejects_replacement_before_kill_and_no_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(tmp_path)
    state = tmp_path / "state" / "record.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"still": "owned"}), encoding="utf-8")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    owner = original.CgroupV2AttemptLease(
        root=tmp_path,
        cgroup=group,
        state_file=state,
        lock_path=tmp_path / "lock",
        policy=CicdResourcePolicy(),
        attempt_root=attempt,
        cgroup_identity=original._exact_cgroup_identity(group),
    )
    renamed = tmp_path / "old"
    group.rename(renamed)
    group.mkdir()
    called: list[str] = []
    monkeypatch.setattr(original, "_kill_cgroup", lambda _group, **_kw: called.append("kill"))
    with pytest.raises(FleetPlanInvalid, match="replaced"):
        owner.close()
    assert called == []
    assert state.is_file()
    assert list(attempt.iterdir()) == []


def test_original_owner_never_publishes_completion_if_native_remove_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = _group(tmp_path)
    state = tmp_path / "state" / "record.json"
    state.parent.mkdir()
    state.write_text("{}", encoding="utf-8")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    owner = original.CgroupV2AttemptLease(
        root=tmp_path,
        cgroup=group,
        state_file=state,
        lock_path=tmp_path / "lock",
        policy=CicdResourcePolicy(),
        attempt_root=attempt,
        cgroup_identity=original._exact_cgroup_identity(group),
    )
    monkeypatch.setattr(original, "_kill_cgroup", lambda _group, **_kw: None)
    monkeypatch.setattr(
        original,
        "_remove_cgroup",
        lambda _group, **_kw: (_ for _ in ()).throw(FleetPlanInvalid("native cleanup incomplete")),
    )
    with pytest.raises(FleetPlanInvalid, match="native cleanup incomplete"):
        owner.close()
    assert state.exists() and list(attempt.iterdir()) == []
