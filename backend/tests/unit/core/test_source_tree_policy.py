from __future__ import annotations

import pytest

from oh_goon.core.source_tree_policy import (
    canonical_repository_relative_path,
    is_package_manager_materialization_path,
    is_transient_source_path,
)


def test_canonical_repository_relative_path_binds_working_directory_lexically() -> None:
    assert (
        canonical_repository_relative_path(
            ".venv/bin/tool",
            working_directory="backend",
        ).as_posix()
        == "backend/.venv/bin/tool"
    )
    assert (
        canonical_repository_relative_path(
            "../frontend/node_modules/.bin/tool",
            working_directory="backend",
        ).as_posix()
        == "frontend/node_modules/.bin/tool"
    )
    assert (
        canonical_repository_relative_path(
            "backend/./.venv/bin/tool",
        ).as_posix()
        == "backend/.venv/bin/tool"
    )


def test_canonical_repository_relative_path_rejects_repository_escape() -> None:
    with pytest.raises(ValueError, match="escapes repository"):
        canonical_repository_relative_path("../../outside", working_directory="backend")


def test_package_manager_materialization_policy_is_location_independent() -> None:
    assert is_package_manager_materialization_path("backend/.venv/bin/tool")
    assert is_package_manager_materialization_path("tools/.venv/bin/tool")
    assert is_package_manager_materialization_path("frontend/node_modules/.bin/tool")
    assert is_package_manager_materialization_path("packages/web/node_modules/.bin/tool")
    assert not is_package_manager_materialization_path("backend/scripts/tool")


@pytest.mark.parametrize(
    "name", ["coverage", "playwright-report", "playwright-runtime-report", "test-results"]
)
def test_frontend_reports_are_transient_only_at_owned_output_roots(name: str) -> None:
    assert is_transient_source_path(f"frontend/{name}/result.txt")
    assert not is_transient_source_path(f"docs/{name}/analysis.md")
    assert not is_transient_source_path(f"frontend/src/{name}/fixture.ts")


def test_local_repository_catalog_is_not_a_global_execution_exclusion() -> None:
    assert not is_transient_source_path("repositories/local/README.md")


@pytest.mark.parametrize(
    "path",
    (
        "src/project.egg-info/PKG-INFO",
        "project.egg-info",
        "project-0.1.dist-info/METADATA",
        "nested/.eggs/dependency.egg/EGG-INFO/PKG-INFO",
    ),
)
def test_generated_python_distribution_metadata_is_transient(path: str) -> None:
    assert is_transient_source_path(path)


@pytest.mark.parametrize(
    "path", ("build/recipe.txt", "dist/fixture.whl", "src/project/dist/schema.json")
)
def test_python_build_directory_names_do_not_exclude_authored_resources(path: str) -> None:
    assert not is_transient_source_path(path)
