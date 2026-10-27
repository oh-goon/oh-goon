from __future__ import annotations

import shutil
import subprocess
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


def test_bash_control_groups_keep_substitutions_and_following_executables(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "scripts/wrapper.sh").write_text(
        "#!/bin/bash\n# OH_GOON_FORWARD_ARGV_FROM: 1\n"
        'if [[ -z "$(id -u)" || "${VALUE:-}" == none ]]; then\n'
        "  (( child = $(getconf _NPROCESSORS_ONLN) )); git --version\n"
        "fi\n"
        "(( cpus > 0 )) || return 2\n"
        'exec systemd-run -- "$@"\n',
        encoding="utf-8",
    )

    authority = derive_shell_fragment_authority(
        root, "bash scripts/wrapper.sh env MODE=1 rm -f artifact", chain=("target",)
    )

    assert authority.unresolved == ()
    assert set(authority.tools) == {"bash", "env", "getconf", "git", "id", "rm", "systemd-run"}


@pytest.mark.parametrize(
    "fragment",
    [
        "[[ -n <(curl --version) ]]; git --version",
        "[[ -n >(curl --version) ]]; git --version",
        "[[ `curl --version` == value ]]; git --version",
        '[[ "`curl --version`" == value ]]; git --version',
    ],
)
def test_conditional_groups_cannot_hide_unowned_native_execution(
    tmp_path: Path, fragment: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root,
        "[[ '<(curl --version)' == \"<(curl --version)\" || "
        "'`curl --version`' == \"\\`curl --version\\`\" || "
        r"\<\(curl\) == \>\(curl\) ]]; git --version",
        chain=("target",),
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert any("unparseable shell fragment" in item for item in authority.unresolved)


@pytest.mark.parametrize(
    "fragment",
    [
        "(( value = <(curl --version) )); git --version",
        "(( value = >(curl --version) )); git --version",
        "(( value = `getconf LONG_BIT` )); git --version",
        '(( value = "`getconf LONG_BIT`" )); git --version',
    ],
)
def test_arithmetic_groups_cannot_hide_unowned_native_execution(
    tmp_path: Path, fragment: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root,
        "printf '%s' '`getconf LONG_BIT`' '<(curl --version)'; "
        '(( value = "(1 + 2)" * 3 )); git --version',
        chain=("target",),
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert any("unparseable shell fragment" in item for item in authority.unresolved)


@pytest.mark.parametrize(
    "fragment",
    [
        "[[ -n ${UNSET:-`curl --version`} ]]; git --version",
        '[[ -n "${UNSET:-`curl --version`}" ]]; git --version',
        "[[ -n \"${UNSET:-'`curl --version`'}\" ]]; git --version",
        "[[ -n ${UNSET:-<(curl --version)} ]]; git --version",
        "[[ -n ${UNSET:->(curl --version)} ]]; git --version",
        "(( value = ${UNSET:-`getconf LONG_BIT`} )); git --version",
        "(( value = ${UNSET:-$((`getconf LONG_BIT`))} )); git --version",
        "(( value = $((`getconf LONG_BIT`)) )); git --version",
        '[[ -n "$((`getconf LONG_BIT`))" ]]; git --version',
        "[[ -n $(printf '%s' ${UNSET:-`curl --version`}) ]]; git --version",
    ],
)
def test_nested_expansions_cannot_hide_unowned_execution_before_control_masking(
    tmp_path: Path, fragment: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root,
        "[[ -n '${UNSET:-`curl --version`}' || "
        r'-n "\${UNSET:-\`curl --version\`}" ]]; '
        "[[ -n ${UNSET:-'`curl --version`'} || "
        r"-n ${UNSET:-\<\(curl\)} ]]; "
        '[[ -n "${UNSET:-$(id -u)}" ]]; '
        "(( value = ${UNSET:-$(getconf LONG_BIT)} )); git --version",
        chain=("target",),
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("getconf", "git", "id")

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert any("unparseable shell fragment" in item for item in authority.unresolved)


@pytest.mark.process
@pytest.mark.parametrize(
    "expression",
    [
        "'`bash scripts/arithmetic-canary.sh`'",
        "'$(bash scripts/arithmetic-canary.sh)'",
    ],
)
@pytest.mark.parametrize(
    "surrounding",
    [
        "(( value = EXPRESSION ))",
        "printf '%s' $(( EXPRESSION ))",
        "printf '%s' \"$(( EXPRESSION ))\"",
        '[[ -n "$(( EXPRESSION ))" ]]',
        "[[ -n ${OH_GOON_ARITHMETIC_UNSET:-$(( EXPRESSION ))} ]]",
    ],
)
def test_native_arithmetic_substitutions_cannot_lose_their_execution_authority(
    tmp_path: Path, expression: str, surrounding: str
) -> None:
    root = _repo(tmp_path)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Native Bash is required for arithmetic authority proof")
    (root / "scripts/arithmetic-canary.sh").write_text(
        "#!/bin/bash\nprintf '1\\n'\nprintf ARITHMETIC_EXECUTION_CANARY >&2\n",
        encoding="utf-8",
    )
    baseline = surrounding.replace("EXPRESSION", "1")
    native_baseline = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", f"unset OH_GOON_ARITHMETIC_UNSET; {baseline}"],
        capture_output=True,
        cwd=root,
        text=True,
        check=False,
        timeout=10,
    )
    assert native_baseline.returncode == 0, native_baseline.stderr
    baseline_authority = derive_shell_fragment_authority(root, baseline, chain=("target",))
    assert baseline_authority.unresolved == ()
    assert baseline_authority.tools == ()

    fragment = surrounding.replace("EXPRESSION", expression)
    native_effect = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", f"unset OH_GOON_ARITHMETIC_UNSET; {fragment}"],
        capture_output=True,
        cwd=root,
        text=True,
        check=False,
        timeout=10,
    )
    # Bash evaluates the substitution before rejecting the resulting quoted
    # arithmetic value. That later syntax failure cannot establish no effects.
    assert native_effect.returncode != 0
    assert native_effect.stderr.startswith("ARITHMETIC_EXECUTION_CANARY")

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert authority.unresolved or (
        "bash" in authority.tools and "scripts/arithmetic-canary.sh" in authority.scripts
    )


def test_control_prefix_assignments_and_reads_preserve_substitution_authority(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    fragment = (
        "while IFS=: read -r hierarchy controllers relative; do :; done; "
        'if ! IFS= read -r limit < input || [[ "$limit" != max ]]; then :; fi; '
        'if ! root=$(realpath .) || ! directory=$(dirname "$root"); then :; fi; '
        "if ! tasks_max=$(getconf CHILD_MAX); then :; fi"
    )

    authority = derive_shell_fragment_authority(root, fragment, chain=("target",))

    assert authority.unresolved == ()
    assert authority.tools == ("dirname", "getconf", "realpath")


@pytest.mark.parametrize(
    ("prefix", "suffix"),
    [("", ""), ("! ", ""), ("if ! ", "; then :; fi"), ("while ", "; do break; done")],
)
def test_assignment_prefixes_keep_actual_executables_and_unknown_variables(
    tmp_path: Path, prefix: str, suffix: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root, f"{prefix}MODE=scan git --version{suffix}", chain=("target",)
    )

    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    dynamic = derive_shell_fragment_authority(
        root, f'{prefix}MODE=scan "$TOOL" --version{suffix}', chain=("target",)
    )

    assert any("unresolved executable variable: $TOOL" in item for item in dynamic.unresolved)


def test_array_assignment_data_keeps_substitutions_and_following_commands(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    authority = derive_shell_fragment_authority(
        root,
        'MODE=scan values=(--limit "$limit" "a)b" "$(getconf CHILD_MAX)"); '
        'values+=("literal || rm"); env MODE=scan git status',
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert authority.tools == ("env", "getconf", "git")


def test_array_assignment_cannot_hide_a_following_dynamic_executable(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root, 'values=(--limit "$limit"); git --version', chain=("target",)
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    authority = derive_shell_fragment_authority(
        root, 'values=(--limit "$limit"); "$TOOL" --version', chain=("target",)
    )

    assert any("unresolved executable variable: $TOOL" in item for item in authority.unresolved)


def test_quoted_array_spelling_does_not_mask_shell_control_commands(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    authority = derive_shell_fragment_authority(
        root, "printf '%s' 'values=(' value || curl --version", chain=("target",)
    )

    assert authority.unresolved == ()
    assert authority.tools == ("curl",)


def test_unclosed_array_assignment_remains_unproven(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(root, 'values=(--limit "$limit")', chain=("target",))
    assert baseline.unresolved == ()
    assert baseline.tools == ()

    authority = derive_shell_fragment_authority(root, 'values=(--limit "$limit"', chain=("target",))

    assert any("unparseable shell fragment" in item for item in authority.unresolved)


@pytest.mark.parametrize(
    "payload",
    ["<(curl --version)", ">(curl --version)", "`curl --version`", '"`curl --version`"'],
)
def test_array_assignment_cannot_hide_unowned_native_execution_forms(
    tmp_path: Path, payload: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root,
        "values=('`curl --version`' '<(curl --version)' '>(curl --version)' "
        r"\`curl\` \<\(curl\)); git --version",
        chain=("target",),
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("git",)

    authority = derive_shell_fragment_authority(
        root, f"values=({payload}); git --version", chain=("target",)
    )

    assert any("unparseable shell fragment" in item for item in authority.unresolved)


def test_arithmetic_variable_names_still_require_authority_when_executed(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root, "(( cpus > 0 )); child --version; cpus --version", chain=("target",)
    )

    assert authority.unresolved == ()
    assert authority.tools == ("child", "cpus")


def test_bash_group_spelling_in_arguments_cannot_hide_later_commands(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root, "printf '%s' [[ value || curl --version ]]", chain=("target",)
    )

    assert authority.unresolved == ()
    assert authority.tools == ("curl",)


def test_literal_cwd_shell_forwarding_skips_zero_and_retains_environment_and_child(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        "sh -c 'cd backend && exec \"$@\"' ignored-zero env MODE=1 git status --porcelain",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert authority.tools == ("env", "git", "sh")


def test_literal_cwd_shell_forwarding_binds_materialized_child_and_preserves_outer_cwd(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)

    authority = derive_shell_fragment_authority(
        root,
        "sh -c 'cd backend && exec \"$@\"' sh env MODE=1 .venv/bin/private-tool; "
        "frontend/node_modules/.bin/other-tool",
        chain=("target",),
    )

    assert authority.unresolved == ()
    assert authority.tools == ("env", "sh")
    assert authority.materialized_executables == (
        "backend/.venv/bin/private-tool",
        "frontend/node_modules/.bin/other-tool",
    )


def test_literal_cwd_shell_forwarding_reads_owned_sources_from_that_directory(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "backend/scripts").mkdir(parents=True)
    (root / "backend/scripts/worker.sh").write_text("#!/bin/sh\nrm -f artifact\n")
    (root / "scripts/worker.sh").write_text("#!/bin/sh\ncurl https://example.invalid\n")

    authority = derive_shell_fragment_authority(
        root, "sh -c 'cd backend && exec \"$@\"' sh sh scripts/worker.sh", chain=("target",)
    )

    assert authority.unresolved == ()
    assert authority.tools == ("rm", "sh")
    assert authority.scripts == ("backend/scripts/worker.sh",)


@pytest.mark.parametrize(
    "program",
    (
        'cd "$DIR" && exec "$@"',
        'cd ../.. && exec "$@"',
        'cd backend || exec "$@"',
        "cd backend && exec '$@'",
        'cd backend && exec "$1"',
        'cd backend && "$@"',
        'cd backend && exec "$@"; curl https://example.invalid',
    ),
)
def test_unproven_shell_positional_forwarding_remains_admission_fatal(
    tmp_path: Path, program: str
) -> None:
    import shlex

    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root, "sh -c 'cd backend && exec \"$@\"' sh git status", chain=("target",)
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("git", "sh")
    authority = derive_shell_fragment_authority(
        root, f"sh -c {shlex.quote(program)} sh git status", chain=("target",)
    )

    assert authority.unresolved
    assert any("unresolved executable" in item for item in authority.unresolved)


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
