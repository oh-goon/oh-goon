from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    _check_dependency_privacy,
)


PRIVACY_MODULE = "oh_goon.repository_tooling.public_dependency_privacy"
CONSUMERS = (
    "backend/src/oh_goon/repository_tooling/dependency_locks.py",
    "backend/src/oh_goon/repository_tooling/release/package_slim.py",
    "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
    "backend/src/oh_goon/repository_tooling/release/accept_container.py",
)


def _write_privacy_boundary(root: Path) -> Path:
    boundary = root / "backend/src/oh_goon/repository_tooling/public_dependency_privacy.py"
    boundary.parent.mkdir(parents=True, exist_ok=True)
    boundary.write_text(
        "def renamed_policy_probe(root):\n"
        "    return ()\n"
        "\n"
        "def renamed_fail_closed_boundary(root):\n"
        "    issues = renamed_policy_probe(root)\n"
        "    if issues:\n"
        "        raise ValueError('dependency privacy rejected')\n",
        encoding="utf-8",
    )
    return boundary


def _write_consumers(root: Path) -> None:
    for index, relative in enumerate(CONSUMERS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if index % 2:
            path.write_text(
                f"from {PRIVACY_MODULE} import renamed_policy_probe as privacy_probe\n"
                "privacy_probe(None)\n",
                encoding="utf-8",
            )
        else:
            path.write_text(
                f"import {PRIVACY_MODULE} as privacy_policy\n"
                "privacy_policy.renamed_fail_closed_boundary(None)\n",
                encoding="utf-8",
            )


def test_dependency_privacy_architecture_ignores_private_helper_spelling(tmp_path: Path) -> None:
    _write_privacy_boundary(tmp_path)
    _write_consumers(tmp_path)

    assert _check_dependency_privacy(tmp_path) == []


def test_dependency_privacy_architecture_requires_semantic_owner_module(tmp_path: Path) -> None:
    _write_consumers(tmp_path)

    violations = _check_dependency_privacy(tmp_path)

    assert any("privacy boundary must be machine-enforced" in item.message for item in violations)


def test_dependency_privacy_architecture_requires_each_semantic_consumer(tmp_path: Path) -> None:
    _write_privacy_boundary(tmp_path)
    _write_consumers(tmp_path)
    missing = tmp_path / CONSUMERS[-1]
    missing.write_text("VALUE = 1\n", encoding="utf-8")

    violations = _check_dependency_privacy(tmp_path)

    assert any(item.path == missing and "must consume" in item.message for item in violations)


def test_dependency_privacy_architecture_contains_no_private_helper_authority() -> None:
    root = Path(__file__).resolve().parents[4]
    architecture = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/_structural_project_dependency_contract.py"
    ).read_text(encoding="utf-8")

    for private_spelling in (
        "PUBLIC_NPM_REGISTRY_HOSTS",
        "frontend_package_lock_privacy_issues",
        "assert_public_source_privacy",
    ):
        assert private_spelling not in architecture
