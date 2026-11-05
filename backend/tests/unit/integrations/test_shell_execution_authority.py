from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.integrations import shell_execution_authority as subject
from oh_goon.integrations.hashmarks import hashmarks_source_scope
from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.shell_execution_authority import derive_shell_fragment_authority


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    return root


def test_literal_owned_source_preserves_tool_need_and_exact_source_evidence(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/worker.sh").write_text("#!/bin/sh\ngit --version\n", encoding="utf-8")
    result = derive_shell_fragment_authority(root, "sh scripts/worker.sh", chain=("target",))
    assert result.unresolved == ()
    assert result.tools == ("git", "sh")
    assert result.scripts == ("scripts/worker.sh",)
    assert any(
        evidence.source_bytes == b"#!/bin/sh\ngit --version\n"
        for evidence in result.repository_evidence
    )


@pytest.mark.parametrize(
    "fragment",
    [
        '"$TOOL" --version',
        'command "$tool"',
        'env "$tool"',
        '"${TOOL}"',
        "$(printf tool)",
        'eval "$tool"',
        "`printf tool`",
    ],
)
def test_unknown_command_heads_cannot_use_ambient_or_declared_environment_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fragment: str
) -> None:
    root = _repo(tmp_path)
    assert (
        derive_shell_fragment_authority(root, "git --version", chain=("target",)).unresolved == ()
    )
    monkeypatch.setenv("TOOL", "curl")
    result = derive_shell_fragment_authority(
        root, fragment, chain=("target",), environment={"TOOL": "curl"}
    )
    assert result.unresolved
    assert "curl" not in result.tools


def test_declared_forwarding_transports_positive_need_without_qualifying_native_behavior(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "scripts/wrapper.sh").write_text(
        '#!/bin/sh\n# OH_GOON_FORWARD_ARGV_FROM: 2\nshift\n"$@"\n', encoding="utf-8"
    )
    result = derive_shell_fragment_authority(
        root, "sh scripts/wrapper.sh label rm -f artifact", chain=("target",)
    )
    assert result.tools == ("rm", "sh")
    assert result.unresolved
    assert any("declared forwarding" in reason for reason in result.unresolved)
    assert any("<declared-child-argv>" in path for tool, path in result.provenance if tool == "rm")


def test_direct_script_name_does_not_establish_a_native_interpreter(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/direct.sh").write_text("#!/usr/bin/env bash\nprintf okay\n", encoding="utf-8")
    result = derive_shell_fragment_authority(root, "scripts/direct.sh", chain=("target",))
    assert result.unresolved
    assert "bash" not in result.tools and "env" not in result.tools


def test_conditional_source_never_selects_a_branch_from_consumer_environment(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    fragment = (
        'MODE=${MODE:-local}\nif [ "$MODE" = local ]; then\n  git status\nelse\n  curl remote\nfi\n'
    )
    first = derive_shell_fragment_authority(
        root, fragment, chain=("target",), environment={"MODE": "local"}
    )
    second = derive_shell_fragment_authority(
        root, fragment, chain=("target",), environment={"MODE": "remote"}
    )
    assert first.unresolved and second.unresolved
    assert first.tools == second.tools
    assert {"git", "curl"} <= set(first.tools)


def test_unavailable_source_producer_cannot_activate_a_shell_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    assert derive_shell_fragment_authority(root, "git --version", chain=("target",)).tools == (
        "git",
    )

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise ValueError("producer source unavailable")

    monkeypatch.setattr(subject, "observe_make_shell_declarations_text", unavailable)
    result = derive_shell_fragment_authority(root, "git --version", chain=("target",))
    assert result.unresolved
    assert result.tools == ()


def test_cached_owned_script_diamond_has_one_shared_work_budget(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scripts/leaf.sh").write_text("git --version\n", encoding="utf-8")
    (root / "scripts/middle.sh").write_text(
        "sh scripts/leaf.sh\nsh scripts/leaf.sh\n", encoding="utf-8"
    )
    (root / "scripts/root.sh").write_text(
        "sh scripts/middle.sh\nsh scripts/middle.sh\n", encoding="utf-8"
    )
    baseline = derive_shell_fragment_authority(root, "sh scripts/root.sh", chain=("target",))
    assert baseline.unresolved == ()
    assert baseline.tools == ("git", "sh")
    with hashmarks_source_scope(root) as scope:
        captured = subject.ShellSourceObservations(root, scope, work_remaining=6)
        result = derive_shell_fragment_authority(
            root, "sh scripts/root.sh", chain=("target",), source_observations=captured
        )
    assert result.unresolved
    assert any("work bound exhausted" in reason for reason in result.unresolved)
    assert "sh" in result.tools


def test_literal_make_prerequisites_use_producer_source_paths(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text(
        "target: child\nchild:\n\tgit --version\nunrelated:\n\tcurl remote\n", encoding="utf-8"
    )
    result = derive_make_target_authority(root, "target")
    assert result.unresolved == ()
    assert result.tools == ("git",)
    assert result.provenance == (("git", "target -> child -> git"),)
    assert result.repository_evidence


def test_make_source_default_stays_conditional_and_never_reads_ambient_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text("TOOL ?= git\ntarget:\n\t$(TOOL) --version\n", encoding="utf-8")
    monkeypatch.setenv("TOOL", "curl")
    result = derive_make_target_authority(root, "target")
    assert result.unresolved
    assert "git" in result.tools
    assert "curl" not in result.tools


@pytest.mark.parametrize(
    "fragment",
    [
        "printf '%s' ${UNSET:-`curl --version`}; git status",
        "[[ -n <(curl --version) ]]; git status",
        "(( value = `getconf LONG_BIT` )); git status",
        "values=($(curl --version)); git status",
        'printf "%s" "$(curl --version)"; git status',
    ],
)
def test_unobserved_native_expansions_cannot_become_complete_source_need(
    tmp_path: Path, fragment: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(root, "git status", chain=("target",))
    assert baseline.unresolved == ()
    result = derive_shell_fragment_authority(root, fragment, chain=("target",))
    assert result.unresolved or {"curl", "getconf"}.intersection(result.tools)


@pytest.mark.parametrize(
    "fragment",
    [
        "env MODE=one rm -f artifact",
        "xargs rm",
        r"find . -exec rm {} \;",
        "timeout 5 rm",
        "nice rm",
        "nohup rm",
        "stdbuf -oL rm",
        "setsid rm",
    ],
)
def test_native_child_operand_semantics_stay_unproven(tmp_path: Path, fragment: str) -> None:
    root = _repo(tmp_path)
    assert derive_shell_fragment_authority(root, "git status", chain=("target",)).unresolved == ()
    result = derive_shell_fragment_authority(root, fragment, chain=("target",))
    assert result.unresolved


@pytest.mark.parametrize(
    "argv,cwd,expected",
    [
        (("backend/./.venv/bin/private-tool",), ".", "backend/.venv/bin/private-tool"),
        ((".venv/bin/private-tool",), "backend", "backend/.venv/bin/private-tool"),
        (("/workspace/backend/.venv/bin/private-tool",), ".", "backend/.venv/bin/private-tool"),
        (
            ("/workspace/frontend/node_modules/.bin/private-tool",),
            ".",
            "frontend/node_modules/.bin/private-tool",
        ),
    ],
)
def test_materialized_paths_use_explicit_original_argv_and_working_directory(
    tmp_path: Path, argv: tuple[str, ...], cwd: str, expected: str
) -> None:
    root = _repo(tmp_path)
    result = derive_shell_fragment_authority(
        root, "ignored source spelling", chain=("target",), argv=argv, working_directory=cwd
    )
    assert result.materialized_executables == (expected,)
    assert result.unresolved == ()


def test_source_cd_does_not_replace_explicit_working_directory(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = derive_shell_fragment_authority(
        root, """sh -c 'cd backend && exec "$@"' sh .venv/bin/private-tool""", chain=("target",)
    )
    assert result.unresolved
    assert "backend/.venv/bin/private-tool" not in result.materialized_executables


@pytest.mark.parametrize(
    "literal", ["/bin/true)", "(/bin/true)", "'/bin/true'", r"backend\.venv\bin\private-tool"]
)
def test_original_argv_punctuation_cannot_be_repaired_into_an_executable(
    tmp_path: Path, literal: str
) -> None:
    root = _repo(tmp_path)
    baseline = derive_shell_fragment_authority(
        root, "unused", chain=("target",), argv=("/bin/true",)
    )
    assert baseline.unresolved == ()
    assert baseline.tools == ("true",)
    result = derive_shell_fragment_authority(root, "unused", chain=("target",), argv=(literal,))
    assert result.unresolved
    assert "true" not in result.tools


def test_dash_uses_the_same_producer_source_boundary(tmp_path: Path) -> None:
    result = derive_shell_fragment_authority(
        _repo(tmp_path), "unused", chain=("target",), argv=("dash", "-c", "git --version")
    )
    assert result.unresolved == ()
    assert result.tools == ("dash", "git")


@pytest.mark.parametrize("program", ["/bin/sh", "/bin/bash", "/bin/dash"])
def test_absolute_shell_spelling_cannot_establish_an_admitted_native_role(
    tmp_path: Path, program: str
) -> None:
    result = derive_shell_fragment_authority(
        _repo(tmp_path), "unused", chain=("target",), argv=(program, "-c", 'exec "$unresolved"')
    )
    assert result.unresolved


def test_closed_original_source_scope_cannot_reuse_cached_shell_observations(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    with hashmarks_source_scope(root) as scope:
        captured = subject.ShellSourceObservations(root, scope)
        baseline = derive_shell_fragment_authority(
            root, "unused", chain=("target",), argv=("/bin/true",), source_observations=captured
        )
        assert baseline.unresolved == ()
    result = derive_shell_fragment_authority(
        root, "unused", chain=("target",), argv=("/bin/true",), source_observations=captured
    )
    assert result.unresolved
    assert result.tools == ()


def test_literal_data_does_not_invent_command_substitution_need(tmp_path: Path) -> None:
    result = derive_shell_fragment_authority(
        _repo(tmp_path), "printf '%s' '$(rm -f artifact)'", chain=("target",)
    )
    assert result.unresolved == ()
    assert result.tools == ()


def test_authority_identity_inputs_change_when_owned_script_bytes_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text("target:\n\tsh scripts/task.sh\n", encoding="utf-8")
    script = root / "scripts/task.sh"
    script.write_text("#!/bin/sh\nprintf one\n", encoding="utf-8")
    before = derive_make_target_authority(root, "target")
    script.write_text("#!/bin/sh\nprintf two\n", encoding="utf-8")
    after = derive_make_target_authority(root, "target")
    assert before.tools == after.tools == ("sh",)
    assert before.source_identities != after.source_identities


def test_materialized_supply_does_not_become_repository_shell_source(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "Makefile").write_text(
        "target:\n\tbackend/.venv/bin/private-tool tests/unit\n", encoding="utf-8"
    )
    before = derive_make_target_authority(root, "target")
    wrapper = root / "backend/.venv/bin/private-tool"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "#!/usr/bin/python3\nfrom package import main\nraise SystemExit(main())\n", encoding="utf-8"
    )
    after = derive_make_target_authority(root, "target")
    assert before.tools == after.tools == ()
    assert (
        before.materialized_executables
        == after.materialized_executables
        == ("backend/.venv/bin/private-tool",)
    )
    assert before.source_identities == after.source_identities


@pytest.mark.process
def test_native_make_parse_time_effect_cannot_be_omitted_by_selected_recipe_scope(
    tmp_path: Path,
) -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("Native Make is required for parse-time authority proof")
    root = _repo(tmp_path)
    makefile = root / "Makefile"
    makefile.write_text("target:\n\t/usr/bin/true\n", encoding="utf-8")
    assert derive_make_target_authority(root, "target").unresolved == ()
    marker = root / "parse-time-marker"
    (root / "scripts/canary.sh").write_text(
        "printf observed > parse-time-marker\n", encoding="utf-8"
    )
    makefile.write_text(
        "SIDE != sh scripts/canary.sh\ntarget:\n\t/usr/bin/true\n", encoding="utf-8"
    )
    result = derive_make_target_authority(root, "target")
    assert result.unresolved
    assert not marker.exists()
    native = subprocess.run(
        [make, "--no-print-directory", "-rR", "target"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    assert marker.read_text(encoding="utf-8") == "observed"


@pytest.mark.process
@pytest.mark.parametrize(
    "source",
    [
        'value="$(bash scripts/assignment-canary.sh)"',
        'values=("$(bash scripts/assignment-canary.sh)")',
    ],
)
def test_native_assignment_and_array_expansion_cannot_hide_child_execution(
    tmp_path: Path, source: str
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Native Bash is required for assignment authority proof")
    root = _repo(tmp_path)
    (root / "scripts/assignment-canary.sh").write_text(
        "printf 1\nprintf ASSIGNMENT_EXECUTION_CANARY >&2\n", encoding="utf-8"
    )
    baseline = derive_shell_fragment_authority(root, "printf literal", chain=("target",))
    assert baseline.unresolved == ()
    result = derive_shell_fragment_authority(root, source, chain=("target",))
    assert result.unresolved or (
        "bash" in result.tools and "scripts/assignment-canary.sh" in result.scripts
    )
    native = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", source],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    assert native.stderr == "ASSIGNMENT_EXECUTION_CANARY"


@pytest.mark.process
def test_native_path_assignment_does_not_qualify_later_literal_program_binding(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Native Bash is required for PATH authority proof")
    root = _repo(tmp_path)
    bindir = root / "private-bin"
    bindir.mkdir()
    program = bindir / "git"
    program.write_text("#!/bin/sh\nprintf observed > path-binding-marker\n", encoding="utf-8")
    program.chmod(0o700)
    assert (
        derive_shell_fragment_authority(root, "git --version", chain=("target",)).unresolved == ()
    )
    source = 'PATH="$PWD/private-bin:$PATH"; git'
    result = derive_shell_fragment_authority(root, source, chain=("target",))
    assert result.unresolved
    assert not (root / "path-binding-marker").exists()
    native = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", source],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert native.returncode == 0, native.stderr
    assert (root / "path-binding-marker").read_text(encoding="utf-8") == "observed"


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
    baseline_authority = derive_shell_fragment_authority(root, "printf literal", chain=("target",))
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
