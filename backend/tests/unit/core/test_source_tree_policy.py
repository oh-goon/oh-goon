from __future__ import annotations

import pytest

from oh_goon.core.source_tree_policy import (
    canonical_repository_relative_path,
    is_package_manager_materialization_path,
)


def test_canonical_repository_relative_path_binds_working_directory_lexically() -> None:
    assert canonical_repository_relative_path(
        ".venv/bin/tool",
        working_directory="backend",
    ).as_posix() == "backend/.venv/bin/tool"
    assert canonical_repository_relative_path(
        "../frontend/node_modules/.bin/tool",
        working_directory="backend",
    ).as_posix() == "frontend/node_modules/.bin/tool"
    assert canonical_repository_relative_path(
        "backend/./.venv/bin/tool",
    ).as_posix() == "backend/.venv/bin/tool"


def test_canonical_repository_relative_path_rejects_repository_escape() -> None:
    with pytest.raises(ValueError, match="escapes repository"):
        canonical_repository_relative_path("../../outside", working_directory="backend")


def test_package_manager_materialization_policy_is_location_independent() -> None:
    assert is_package_manager_materialization_path("backend/.venv/bin/tool")
    assert is_package_manager_materialization_path("tools/.venv/bin/tool")
    assert is_package_manager_materialization_path("frontend/node_modules/.bin/tool")
    assert is_package_manager_materialization_path("packages/web/node_modules/.bin/tool")
    assert not is_package_manager_materialization_path("backend/scripts/tool")
