from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from oh_goon.integrations.hashmarks.dependencies import (
    assess_locked_dependencies,
    observe_locked_dependencies,
)
from tests.factories.dependency_observations import dependency_snapshot


def test_lock_inventory_excludes_application_and_does_not_read_declarations(tmp_path: Path):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    omitted = tmp_path / "packages" / "omitted"
    omitted.mkdir(parents=True)
    (omitted / "pyproject.toml").write_text('[project]\ndependencies=["excluded-package"]\n')
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers=["packages/*"]\nexclude=["packages/omitted"]\n'
    )
    current = observe_locked_dependencies(tmp_path)
    assert [item.package_name for item in current.dependencies] == ["requests"]
    assert current.evidence.absence_admissible
    assert current.sources == observed.sources
    assert current.dependencies[0].relationships
    assert current.producer_observation["roots"]


def test_unsupported_and_missing_locks_are_not_empty_complete_inventories(tmp_path: Path):
    missing = observe_locked_dependencies(tmp_path)
    assert missing.evidence.availability == "unavailable"
    (tmp_path / "uv.lock").write_text("version=999\nrevision=1\n")
    unsupported = observe_locked_dependencies(tmp_path)
    assert unsupported.evidence.availability == "unsupported"
    assert not unsupported.evidence.absence_admissible
    assert unsupported.dependencies == ()


def test_retained_lock_evidence_becomes_stale_after_an_equal_size_edit(tmp_path: Path):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    path = tmp_path / "uv.lock"
    path.write_text(path.read_text().replace("2.32.0", "2.32.1"))
    result = assess_locked_dependencies(tmp_path, observed)
    assert result.evidence.freshness == "stale"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [("missing-context", "unknown"), ("incomplete", "incomplete"), ("truncated", "incomplete")],
)
def test_producer_inventory_coverage_controls_lock_absence(
    tmp_path: Path, coverage: str, expected: str
):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    adapter = deepcopy(observed.adapter_observation)
    if coverage == "missing-context":
        adapter["contexts"].append("uncovered")
    else:
        for row in adapter["coverage"]:
            if row["kind"] == "resolved-inventory":
                row["completeness"] = "incomplete"
                row["truncation"] = "truncated" if coverage == "truncated" else "complete"
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(adapter)
    snapshot = observed.model_copy(update={"producer_observation": retained})
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "current"
    assert result.evidence.completeness == expected
    assert not result.evidence.absence_admissible


def test_matching_claim_does_not_repair_unknown_native_lock_observation(tmp_path: Path):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    lock = tmp_path / "uv.lock"
    content = lock.read_bytes()
    lock.unlink()
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(observed.adapter_observation)
    lock.write_bytes(content)
    snapshot = observed.model_copy(update={"producer_observation": retained})
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "unknown"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize("claimed_revision", [None, "0" * 64])
def test_external_revision_claim_does_not_relabel_native_source_freshness(
    tmp_path: Path, claimed_revision: str | None
):
    from hashmarks import CodeMap

    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    adapter = deepcopy(observed.adapter_observation)
    adapter["repository_inputs"][0]["member_revision"] = claimed_revision
    with CodeMap(tmp_path, state_dir=tmp_path / "observer-state") as native:
        retained = native.dependencies.observe(adapter)
    snapshot = observed.model_copy(update={"producer_observation": retained})
    result = assess_locked_dependencies(tmp_path, snapshot)

    assert result.evidence.freshness == "current"
    assert not result.evidence.absence_admissible


@pytest.mark.parametrize("mutation", ["delete", "deny"])
def test_retained_lock_must_remain_available_for_absence(tmp_path: Path, mutation: str):
    observed = dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    if mutation == "delete":
        (tmp_path / "uv.lock").unlink()
    else:
        (tmp_path / ".hashmarks-context.toml").write_text(
            '[[rule]]\npattern="uv.lock"\nindex=false\n'
        )
    result = assess_locked_dependencies(tmp_path, observed)

    assert result.evidence.availability == "unavailable"
    assert not result.evidence.absence_admissible


def test_conditional_relationships_remain_qualified_facts(tmp_path: Path):
    dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    path = tmp_path / "uv.lock"
    path.write_text(
        path.read_text().replace(
            '{name="requests"}', '{name="requests", marker="sys_platform == \'win32\'"}'
        )
    )
    observed = observe_locked_dependencies(tmp_path)
    assert observed.evidence.availability == "available"
    assert observed.dependencies[0].relationships[0]["marker"] == "sys_platform == 'win32'"


def test_native_policy_denies_lock_acquisition_before_interpretation(tmp_path: Path):
    dependency_snapshot(tmp_path, ("requests", "2.32.0"))
    (tmp_path / ".hashmarks-context.toml").write_text('[[rule]]\npattern="uv.lock"\nindex=false\n')
    result = observe_locked_dependencies(tmp_path)
    assert result.evidence.availability == "unavailable"
    assert not result.evidence.absence_admissible
    assert result.adapter_observation == {}


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlink support unavailable")
def test_symlink_lock_is_not_opened_as_dependency_evidence(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.write_text("not a lock\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").symlink_to(outside)
    result = observe_locked_dependencies(repo)
    assert result.evidence.availability == "unavailable"
    assert result.adapter_observation == {}
