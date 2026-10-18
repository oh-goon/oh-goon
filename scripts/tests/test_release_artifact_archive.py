from __future__ import annotations

import io
from pathlib import Path
import zipfile

import pytest

from oh_goon.repository_tooling.release.check_release_artifact import (
    _archive_issues,
    _expected_payload,
    release_artifact_issues,
)


def _minimal_root(tmp_path: Path) -> Path:
    (tmp_path / "backend/src/oh_goon").mkdir(parents=True)
    (tmp_path / "generated/release/static").mkdir(parents=True)
    (tmp_path / "templates/goons").mkdir(parents=True)
    (tmp_path / "templates/plans").mkdir(parents=True)
    (tmp_path / "backend/pyproject.toml").write_text(
        '[project]\nname="oh-goon"\nversion="1.0.0"\n[project.scripts]\noh-goon="oh_goon.cli:main"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_archive_rejects_duplicate_entries(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    payload = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("oh_goon/a.py", "one")
            archive.writestr("oh_goon/a.py", "two")
    payload.seek(0)
    with zipfile.ZipFile(payload) as archive:
        issues = _archive_issues(archive, root, "1.0.0")
    assert "release wheel contains duplicate entries" in issues


def test_managed_source_rejects_symlink_directory(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "backend/src/oh_goon/linked").symlink_to(outside, target_is_directory=True)
    _payload, issues = _expected_payload(root)
    assert any("managed release source contains symlink" in issue for issue in issues)


def test_release_artifact_rejects_symlink_wheel(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    dist = root / "generated/release/dist"
    dist.mkdir(parents=True)
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"not-a-wheel")
    (dist / "oh_goon-1.0.0-py3-none-any.whl").symlink_to(outside)
    assert release_artifact_issues(root) == (
        "release wheel must be a contained regular non-symlink file",
    )


def test_release_artifact_rejects_extra_dist_payload(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    dist = root / "generated/release/dist"
    dist.mkdir(parents=True)
    (dist / "oh_goon-1.0.0-py3-none-any.whl").write_bytes(b"not-a-wheel")
    (dist / "unexpected.txt").write_text("stale", encoding="utf-8")
    assert release_artifact_issues(root) == (
        "release dist must contain only the release wheel",
    )


def test_release_artifact_rejects_noncanonical_version_identity(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    (root / "VERSION").write_text("v1.0\n", encoding="utf-8")
    assert release_artifact_issues(root) == (
        "VERSION must contain canonical x.y.z release identity, got 'v1.0'",
    )


def test_expected_payload_rejects_symlinked_managed_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    source_parent = tmp_path / "backend" / "src"
    source_parent.mkdir(parents=True)
    (source_parent / "oh_goon").symlink_to(outside, target_is_directory=True)
    for path in (
        tmp_path / "generated/release/static",
        tmp_path / "templates/goons",
        tmp_path / "templates/plans",
    ):
        path.mkdir(parents=True, exist_ok=True)
    _payload, issues = _expected_payload(tmp_path)
    assert any("contained regular non-symlink directory" in issue for issue in issues)
