from __future__ import annotations

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
