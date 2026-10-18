from __future__ import annotations

import io
import zipfile
from pathlib import Path

from oh_goon.repository_tooling.release.check_release_artifact import _entry_point_issues, _project_scripts


def test_project_scripts_reads_console_entry_points(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[project]\nname="oh-goon"\nversion="1.0.0"\n[project.scripts]\noh-goon="oh_goon.cli:main"\n',
        encoding="utf-8",
    )
    assert _project_scripts(tmp_path) == {"oh-goon": "oh_goon.cli:main"}


def test_entry_points_reject_extra_console_script(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[project]\nname="oh-goon"\nversion="1.0.0"\n[project.scripts]\noh-goon="oh_goon.cli:main"\n',
        encoding="utf-8",
    )
    payload = io.BytesIO()
    entry = "oh_goon-1.0.0.dist-info/entry_points.txt"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            entry,
            "[console_scripts]\noh-goon=oh_goon.cli:main\nunexpected=oh_goon.cli:main\n",
        )
    payload.seek(0)
    with payload, zipfile.ZipFile(payload) as archive:
        issues = _entry_point_issues(archive, entry, tmp_path)
    assert issues == [
        "release wheel console_scripts do not match backend/pyproject.toml"
    ]
