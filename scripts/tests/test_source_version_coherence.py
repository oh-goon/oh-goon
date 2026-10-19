from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling.generation.generate_api_types import (
    render_generated_files,
)
from oh_goon.repository_tooling.release import package_slim
from oh_goon.repository_tooling.release.source_version_coherence import (
    source_version_issues,
)


def _write_version_owners(root: Path, version: str = "1.2.3") -> None:
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "generated/openapi").mkdir(parents=True, exist_ok=True)
    (root / "docs/certification/questions").mkdir(parents=True, exist_ok=True)
    (root / "backend/pyproject.toml").write_text(
        f'[project]\nname="oh-goon"\nversion="{version}"\n', encoding="utf-8"
    )
    (root / "frontend/package.json").write_text(
        json.dumps({"name": "oh-goon-frontend", "version": version}) + "\n",
        encoding="utf-8",
    )
    lock = {
        "name": "oh-goon-frontend",
        "version": version,
        "lockfileVersion": 3,
        "packages": {"": {"name": "oh-goon-frontend", "version": version}},
    }
    (root / "frontend/package-lock.json").write_text(
        json.dumps(lock) + "\n", encoding="utf-8"
    )
    openapi = {
        "components": {"schemas": {}},
        "info": {"title": "fixture", "version": version},
        "openapi": "3.1.0",
        "paths": {},
    }
    (root / "generated/openapi/openapi.json").write_text(
        json.dumps(openapi) + "\n", encoding="utf-8"
    )
    (root / "docs/certification/questions/question-metadata.yml").write_text(
        f"schema_version: 2\nsource_version: {version}\n", encoding="utf-8"
    )


def test_package_slim_rejects_openapi_release_version_drift(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _write_version_owners(root)
    # Packaging validates public dependency privacy before release identity.
    # Supply a minimal regular lock so this behavioral test reaches the
    # release-identity boundary it is intended to exercise.
    (root / "backend/uv.lock").write_text("version = 1\n", encoding="utf-8")
    openapi_path = root / "generated/openapi/openapi.json"
    captured = json.loads(openapi_path.read_text(encoding="utf-8"))
    captured["info"]["version"] = "1.2.2"
    openapi_path.write_text(json.dumps(captured) + "\n", encoding="utf-8")
    generated_frontend = root / "generated/frontend"
    generated_frontend.mkdir(parents=True)
    for relative, content in render_generated_files(captured).items():
        destination = generated_frontend / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="refuses release identity drift") as exc:
        package_slim.package_slim(tmp_path / "out.zip", root=root)

    assert (
        "generated/openapi/openapi.json version '1.2.2' does not match VERSION '1.2.3'"
        in str(exc.value)
    )
    assert not (tmp_path / "out.zip").exists()


@pytest.mark.parametrize(
    ("owner", "replacement", "expected_owner"),
    [
        (
            "backend/pyproject.toml",
            '[project]\nname="oh-goon"\nversion="9.9.9"\n',
            "backend/pyproject.toml",
        ),
        (
            "frontend/package.json",
            '{"name":"oh-goon-frontend","version":"9.9.9"}\n',
            "frontend/package.json",
        ),
        (
            "frontend/package-lock.json",
            '{"name":"oh-goon-frontend","version":"9.9.9","lockfileVersion":3,'
            '"packages":{"":{"name":"oh-goon-frontend","version":"1.2.3"}}}\n',
            "frontend/package-lock.json",
        ),
        (
            "docs/certification/questions/question-metadata.yml",
            "schema_version: 2\nsource_version: 9.9.9\n",
            "docs/certification/questions/question-metadata.yml",
        ),
    ],
)
def test_release_version_coherence_rejects_each_source_owner_drift(
    tmp_path: Path, owner: str, replacement: str, expected_owner: str
) -> None:
    root = tmp_path / "root"
    _write_version_owners(root)
    (root / owner).write_text(replacement, encoding="utf-8")

    issues = source_version_issues(root, "1.2.3")

    assert any(issue.startswith(expected_owner) for issue in issues)
    assert all("9.9.9" in issue for issue in issues)


def test_release_version_coherence_rejects_package_lock_root_entry_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    _write_version_owners(root)
    package_lock = root / "frontend/package-lock.json"
    payload = json.loads(package_lock.read_text(encoding="utf-8"))
    payload["packages"][""]["version"] = "9.9.9"
    package_lock.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert source_version_issues(root, "1.2.3") == (
        "frontend/package-lock.json packages[''] version '9.9.9' does not match VERSION '1.2.3'",
    )


def test_current_repository_release_identity_is_coherent() -> None:
    """Regression: a development candidate must not bump VERSION alone."""
    root = Path(__file__).resolve().parents[2]
    expected = (root / "VERSION").read_text(encoding="utf-8").strip()

    assert source_version_issues(root, expected) == ()
