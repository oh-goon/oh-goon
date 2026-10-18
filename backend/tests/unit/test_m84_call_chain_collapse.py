from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.integrations import shell_execution_authority as shell_authority


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _derive(root: Path, fragment: str):
    return shell_authority.derive_shell_fragment_authority(
        root,
        fragment,
        chain=("step:check",),
    )


def test_nested_indirect_launchers_keep_direct_semantic_handlers(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    authority = _derive(
        root,
        "env MODE=copy timeout 5 nice -n 3 nohup stdbuf -oL setsid rm -f artifact",
    )

    assert authority.unresolved == ()
    assert set(authority.tools) == {
        "env",
        "nice",
        "nohup",
        "rm",
        "setsid",
        "stdbuf",
        "timeout",
    }
    assert not hasattr(shell_authority, "_scan_indirect_command")


def test_xargs_and_find_keep_nested_child_scanning(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    xargs = _derive(root, "printf x | xargs -r env MODE=copy sh -c 'rm -f artifact'")
    find = _derive(root, r"find . -exec env MODE=copy timeout 5 rm -f {} \;")

    assert xargs.unresolved == ()
    assert set(xargs.tools) == {"env", "rm", "sh", "xargs"}
    assert find.unresolved == ()
    assert set(find.tools) == {"env", "find", "rm", "timeout"}


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("printf x | xargs -r", "xargs command is implicit"),
        ("timeout --unknown 5 rm -f artifact", "timeout child command is unprovable"),
        (r"find . -exec", "find -exec command is missing"),
    ],
)
def test_collapsed_dispatch_preserves_fail_closed_indirect_forms(
    tmp_path: Path,
    fragment: str,
    expected: str,
) -> None:
    root = _repo(tmp_path)

    authority = _derive(root, fragment)

    assert authority.unresolved
    assert any(expected in item for item in authority.unresolved)


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

    npm = _derive(root, "npm run verify")
    unmatched = _derive(root, "rm -f artifact")
    dynamic = _derive(root, '"$TOOL" --version')

    assert npm.unresolved == ()
    assert set(npm.tools) == {"npm", "rm", "sh"}
    assert unmatched.unresolved == ()
    assert unmatched.tools == ("rm",)
    assert any("unresolved executable variable: $TOOL" in item for item in dynamic.unresolved)
