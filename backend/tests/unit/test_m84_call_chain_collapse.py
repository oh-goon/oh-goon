from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.integrations import shell_execution_authority as shell_authority


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _derive(root: Path, fragment: str, *, working_directory: str = "."):
    return shell_authority.derive_shell_fragment_authority(
        root,
        fragment,
        chain=("step:check",),
        working_directory=working_directory,
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "env MODE=copy timeout 5 nice -n 3 nohup stdbuf -oL setsid rm -f artifact",
        "printf x | xargs -r env MODE=copy sh -c 'rm -f artifact'",
        r"find . -exec env MODE=copy timeout 5 rm -f {} \;",
    ],
)
def test_nested_native_launchers_cannot_claim_complete_child_authority(
    tmp_path: Path, fragment: str
) -> None:
    root = _repo(tmp_path)
    artifact = root / "artifact"
    artifact.write_text("retained", encoding="utf-8")

    baseline = _derive(root, "rm -f artifact")
    assert baseline.unresolved == ()
    assert baseline.tools == ("rm",)

    authority = _derive(root, fragment)

    assert authority.unresolved
    assert artifact.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize(
    "fragment",
    [
        "printf x | xargs -r",
        "timeout --unknown 5 rm -f artifact",
        r"find . -exec",
    ],
)
def test_malformed_native_launchers_remain_unproven(
    tmp_path: Path,
    fragment: str,
) -> None:
    root = _repo(tmp_path)
    assert _derive(root, "rm -f artifact").unresolved == ()

    authority = _derive(root, fragment)

    assert authority.unresolved


def test_npm_and_unmatched_commands_keep_existing_owners(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps({"name": "fixture", "scripts": {"verify": "rm -f artifact"}}) + "\n",
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "fixture"}}}) + "\n",
        encoding="utf-8",
    )

    wrong_directory = _derive(root, "npm run verify")
    npm = _derive(root, "npm run verify", working_directory="frontend")
    unmatched = _derive(root, "rm -f artifact")
    dynamic = _derive(root, '"$TOOL" --version')

    assert npm.unresolved
    assert any("script-shell" in reason for reason in npm.unresolved)
    assert set(npm.package_sources) == {"frontend/package.json", "frontend/package-lock.json"}
    assert "sh" not in npm.tools
    assert unmatched.unresolved == ()
    assert unmatched.tools == ("rm",)
    assert dynamic.unresolved
    assert dynamic.tools == ()
    assert wrong_directory.unresolved
    assert "rm" not in wrong_directory.tools
