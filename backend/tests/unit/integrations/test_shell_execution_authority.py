from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.shell_execution_authority import derive_shell_fragment_authority


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    return root


def test_owned_script_closure_derives_indirect_launches(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/worker.sh").write_text(
        r"""#!/bin/sh
set -eu
find work -type f -exec rm -f {} \\\;
printf '%s\\n' item | xargs -r env MODE=copy sh -c 'cp source target'
""",
        encoding="utf-8",
    )

    authority = derive_shell_fragment_authority(
        root,
        "sh scripts/worker.sh",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert set(authority.tools) == {"cp", "env", "find", "rm", "sh", "xargs"}
    assert "scripts/worker.sh" in authority.scripts


def test_owned_script_dynamic_executable_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/dynamic.sh").write_text(
        """#!/bin/sh
set -eu
"$TOOL" --version
""",
        encoding="utf-8",
    )

    authority = derive_shell_fragment_authority(
        root,
        "sh scripts/dynamic.sh",
        chain=("target",),
    )

    assert authority.unresolved
    assert any("unresolved executable variable: $TOOL" in item for item in authority.unresolved)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ('p=backend/.venv/bin/private-tool; "$p"', "unresolved executable variable"),
        ('command "$tool"', "unresolved executable variable"),
        ('env "$tool"', "unresolved executable variable"),
        ('"${TOOL}"', "unresolved executable parameter expansion"),
        ("$(printf tool)", "unresolved executable command substitution"),
        ('eval "$tool"', "dynamic eval execution is unprovable"),
        ("`printf tool`", "unresolved executable token"),
    ],
)
def test_dynamic_executable_forms_fail_closed(
    tmp_path: Path,
    fragment: str,
    expected: str,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(root, fragment, chain=("step:check",))

    assert authority.unresolved
    assert any(expected in item for item in authority.unresolved)


def test_direct_owned_script_binds_shebang_interpreter(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    script = root / "scripts/direct.sh"
    script.write_text("#!/usr/bin/env bash\nprintf 'ok\\n'\n", encoding="utf-8")

    authority = derive_shell_fragment_authority(
        root,
        "scripts/direct.sh",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert set(authority.tools) == {"bash", "env"}


def test_forwarding_contract_resolves_static_forwarded_command(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/wrapper.sh").write_text(
        """#!/bin/sh
# OH_GOON_FORWARD_ARGV_FROM: 2
label=$1
shift
"$@"
""",
        encoding="utf-8",
    )

    authority = derive_shell_fragment_authority(
        root,
        "sh scripts/wrapper.sh label env MODE=1 sh -c 'rm -f artifact'",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert set(authority.tools) == {"env", "rm", "sh"}


def test_authority_identity_inputs_change_when_owned_script_bytes_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text("target:\n\tsh scripts/task.sh\n", encoding="utf-8")
    script = root / "scripts/task.sh"
    script.write_text("#!/bin/sh\nprintf 'one\\n'\n", encoding="utf-8")

    before = derive_make_target_authority(root, "target")
    script.write_text("#!/bin/sh\nprintf 'two\\n'\n", encoding="utf-8")
    after = derive_make_target_authority(root, "target")

    assert before.tools == after.tools == ("sh",)
    assert before.source_identities != after.source_identities


def test_single_quoted_command_substitution_does_not_expand_authority(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        "printf '%s\\n' '$(rm -f artifact)'",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert authority.tools == ()


def test_double_quoted_command_substitution_is_part_of_authority(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        'printf "%s\\n" "$(rm -f artifact)"',
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert authority.tools == ("rm",)


def test_materialized_venv_console_wrapper_cannot_change_static_authority(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Makefile").write_text(
        "target:\n\tbackend/.venv/bin/oh-goon-test-batches tests/unit\n",
        encoding="utf-8",
    )

    before = derive_make_target_authority(root, "target")
    wrapper = root / "backend/.venv/bin/oh-goon-test-batches"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "#!/usr/bin/python3\n"
        "from oh_goon.repository_tooling.test_batches import main\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    after = derive_make_target_authority(root, "target")

    assert before == after
    assert "__name__" not in after.tools
    assert "from" not in after.tools
    assert "raise" not in after.tools
    assert "python3" not in after.tools
    assert after.materialized_executables == ("backend/.venv/bin/oh-goon-test-batches",)


def test_materialized_executable_identity_is_canonical_across_shell_cwd_aliases(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    forms = {
        "backend/./.venv/bin/private-tool": "backend/.venv/bin/private-tool",
        "cd backend && .venv/bin/private-tool": "backend/.venv/bin/private-tool",
        "(cd backend && .venv/bin/private-tool)": "backend/.venv/bin/private-tool",
        "sh -c 'cd backend && .venv/bin/private-tool'": "backend/.venv/bin/private-tool",
        "cd packages/web && node_modules/.bin/private-tool": (
            "packages/web/node_modules/.bin/private-tool"
        ),
    }

    for fragment, expected in forms.items():
        authority = derive_shell_fragment_authority(root, fragment, chain=("target",))
        assert expected in authority.materialized_executables


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        (
            "/workspace/backend/.venv/bin/private-tool",
            "backend/.venv/bin/private-tool",
        ),
        (
            "sh -c '/workspace/backend/.venv/bin/private-tool'",
            "backend/.venv/bin/private-tool",
        ),
        (
            "/workspace/frontend/node_modules/.bin/private-tool",
            "frontend/node_modules/.bin/private-tool",
        ),
        (
            "sh -c '/workspace/frontend/node_modules/.bin/private-tool'",
            "frontend/node_modules/.bin/private-tool",
        ),
    ],
)
def test_materialized_executable_identity_normalizes_canonical_sandbox_workspace_alias(
    tmp_path: Path,
    fragment: str,
    expected: str,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert authority.materialized_executables == (expected,)


def test_repo_absolute_path_is_not_treated_as_internal_sandbox_alias(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        "/repo/backend/.venv/bin/private-tool",
        chain=("target",),
    )

    assert authority.materialized_executables == ()
    assert authority.tools == ("private-tool",)


def test_materialized_executable_identity_uses_initial_step_working_directory(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        ".venv/bin/private-tool",
        chain=("step:check",),
        working_directory="backend",
    )

    assert authority.materialized_executables == ("backend/.venv/bin/private-tool",)


def test_materialized_executable_identity_tracks_literal_cd_from_step_working_directory(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        "cd .. && backend/.venv/bin/private-tool",
        chain=("step:check",),
        working_directory="backend",
    )

    assert authority.materialized_executables == ("backend/.venv/bin/private-tool",)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("xargs backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("printf x | xargs backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        (r"find . -exec backend/.venv/bin/private-tool {} \;", "backend/.venv/bin/private-tool"),
        (
            r"find . -execdir frontend/node_modules/.bin/private-tool {} \;",
            "frontend/node_modules/.bin/private-tool",
        ),
        ("timeout 5 backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("nice backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("nohup backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("stdbuf -oL backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        ("setsid backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
    ],
)
def test_child_launchers_expose_materialized_executable_authority(
    tmp_path: Path,
    fragment: str,
    expected: str,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(root, fragment, chain=("step:check",))

    assert authority.unresolved == ()
    assert authority.materialized_executables == (expected,)


@pytest.mark.parametrize(
    ("fragment", "expected_tools"),
    [
        ("timeout 5 rm -f artifact", {"timeout", "rm"}),
        ("nice rm -f artifact", {"nice", "rm"}),
        ("nohup rm -f artifact", {"nohup", "rm"}),
        ("stdbuf -oL rm -f artifact", {"stdbuf", "rm"}),
        ("setsid rm -f artifact", {"setsid", "rm"}),
    ],
)
def test_process_launchers_share_child_tool_authority_traversal(
    tmp_path: Path,
    fragment: str,
    expected_tools: set[str],
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(root, fragment, chain=("step:check",))

    assert authority.unresolved == ()
    assert set(authority.tools) == expected_tools


@pytest.mark.parametrize(
    "fragment",
    [
        "timeout --unknown 5 backend/.venv/bin/private-tool",
        "nice --unknown backend/.venv/bin/private-tool",
        "nohup --unknown backend/.venv/bin/private-tool",
        "stdbuf --unknown backend/.venv/bin/private-tool",
        "setsid --unknown backend/.venv/bin/private-tool",
    ],
)
def test_process_launchers_fail_closed_when_child_argv_is_not_provable(
    tmp_path: Path,
    fragment: str,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(root, fragment, chain=("step:check",))

    assert authority.unresolved
    assert any("child command is unprovable" in item for item in authority.unresolved)


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("printf x | xargs backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
        (r"find . -exec backend/.venv/bin/private-tool {} \;", "backend/.venv/bin/private-tool"),
        ("timeout 5 backend/.venv/bin/private-tool", "backend/.venv/bin/private-tool"),
    ],
)
def test_make_authority_uses_same_child_launcher_materialization_traversal(
    tmp_path: Path,
    recipe: str,
    expected: str,
) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text(f"target:\n\t{recipe}\n", encoding="utf-8")

    authority = derive_make_target_authority(root, "target")

    assert authority.unresolved == ()
    assert authority.materialized_executables == (expected,)


def test_multiple_find_exec_children_share_materialization_traversal(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fragment = (
        r"find . -exec true {} \; "
        r"-exec frontend/node_modules/.bin/private-tool {} \;"
    )

    authority = derive_shell_fragment_authority(root, fragment, chain=("step:check",))

    assert authority.unresolved == ()
    assert authority.tools == ("find",)
    assert authority.materialized_executables == ("frontend/node_modules/.bin/private-tool",)
