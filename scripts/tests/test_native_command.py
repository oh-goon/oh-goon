from __future__ import annotations

import ast
import json
import os
import sys

from typing import Any
from collections.abc import Sequence

import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template
from oh_goon.repository_tooling import native_command

ROOT = Path(__file__).resolve().parents[2]


def _has_canonical_banner_command(source: str) -> bool:
    expected = ast.dump(
        ast.parse("('sh', str(_COMMAND_BANNER), label, detail)", mode="eval").body
    )
    return any(
        ast.dump(node.value) == expected
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
    )


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    (
        (2, "connection reset by peer"),
        (69, "temporary failure in name resolution"),
        (75, "service unavailable; retry later"),
    ),
)
def test_native_command_runs_exactly_once_for_transient_looking_failures(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stderr: str,
) -> None:
    seen: list[tuple[str, ...]] = []
    command = ("uv", "sync", "--project", "backend", "--frozen")

    def fake_run(
        args: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(tuple(args))
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert (
        native_command.run_native_command(command, show_command_banner=False)
        == returncode
    )
    assert seen == [command]


def test_native_command_process_start_error_is_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[tuple[str, ...]] = []
    command = ("uv", "sync", "--project", "backend", "--frozen")

    def fake_run(
        args: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(tuple(args))
        raise OSError("temporary process start failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert native_command.main(["--no-command-banner", "--", *command]) == 127
    assert seen == [command]
    assert "Failure class: process_start" in capsys.readouterr().err


@pytest.mark.parametrize("returncode", [0, 75])
def test_native_command_cli_preserves_native_inputs_and_redacts_only_display(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    returncode: int,
) -> None:
    private_root = tmp_path / "private=workspace"
    private_artifacts = private_root / "artifacts"
    receipt = tmp_path / "native-attempts.jsonl"
    command = (
        sys.executable,
        "-c",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(sys.argv[1]).open('a', encoding='utf-8') as output:\n"
        "    output.write(json.dumps({'argv': sys.argv[2:], 'cwd': str(Path.cwd()), "
        "'environment': os.environ.get('OH_GOON_NATIVE_LABEL')}) + '\\n')\n"
        "print('created ' + sys.argv[2])\n"
        "print('temporary failure ' + sys.argv[3], file=sys.stderr)\n"
        "raise SystemExit(int(sys.argv[4]))\n",
        str(receipt),
        str(private_root / "source"),
        str(private_artifacts / "artifact"),
        str(returncode),
    )
    monkeypatch.delenv("OH_GOON_NATIVE_LABEL", raising=False)
    assert (
        native_command.main(
            [
                "--cwd",
                str(tmp_path),
                "--env",
                f"OH_GOON_NATIVE_LABEL={private_root}",
                "--path-label",
                f"{private_root}=<repo>",
                "--path-label",
                f"{private_artifacts}=<artifacts>",
                "--",
                *command,
            ]
        )
        == returncode
    )
    attempts = [
        json.loads(row) for row in receipt.read_text(encoding="utf-8").splitlines()
    ]
    assert attempts == [
        {
            "argv": list(command[4:]),
            "cwd": str(tmp_path),
            "environment": str(private_root),
        }
    ]
    captured = capfd.readouterr()
    assert str(private_root) not in captured.out + captured.err
    assert "Command:" in captured.out
    assert "created <repo>/source" in captured.out
    assert "temporary failure <artifacts>/artifact" in captured.err
    if returncode:
        assert f"Exit code: {returncode}" in captured.err
    assert "OH_GOON_NATIVE_LABEL" not in os.environ


def test_native_command_cli_rejects_non_absolute_display_path_label() -> None:
    with pytest.raises(SystemExit, match="2"):
        native_command.main(["--path-label", "relative=<repo>", "--", "uv", "sync"])


def test_native_command_banner_runs_with_declared_posix_shell_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_sh = shutil.which("sh")
    assert host_sh is not None
    (tmp_path / "sh").symlink_to(Path(host_sh).resolve())
    # Reproduce strict certification authority: sh is admitted, while a stray bash
    # name must not become a hidden executable dependency of native-command banners.
    (tmp_path / "bash").write_text("not executable\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    seen: list[tuple[str, ...]] = []

    def fake_run(
        args: Sequence[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert native_command.main(["--", "uv", "sync", "--frozen"]) == 0
    assert seen == [("uv", "sync", "--frozen")]


def test_native_command_cli_applies_environment_only_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, str] | None] = []

    def fake_run(
        _args: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(("uv", "sync"), 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert (
        native_command.main(
            ["--env", "UV_PROJECT_ENVIRONMENT=/tmp/venv", "--", "uv", "sync"]
        )
        == 0
    )
    assert seen and seen[0] is not None
    assert seen[0]["UV_PROJECT_ENVIRONMENT"] == "/tmp/venv"


def test_native_command_preserves_operator_uv_configuration_without_printing_secret_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, str] | None] = []

    def fake_run(
        _args: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(("uv", "sync"), 0, stdout="", stderr="")

    secret_index = "https://user:secret@internal.example/simple"
    monkeypatch.setenv("UV_DEFAULT_INDEX", secret_index)
    monkeypatch.setenv("UV_SYSTEM_CERTS", "true")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert native_command.main(["--owner", "uv", "--", "uv", "sync", "--frozen"]) == 0
    assert seen and seen[0] is not None
    assert seen[0]["UV_DEFAULT_INDEX"] == secret_index
    output = capsys.readouterr().out
    assert "UV_DEFAULT_INDEX set (inherited)" in output
    assert "UV_SYSTEM_CERTS=true (inherited)" in output
    assert secret_index not in output
    assert "secret" not in output


def test_native_command_strips_ambient_virtual_env_from_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, str] | None] = []

    def fake_run(
        _args: Sequence[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(("uv", "sync"), 0, stdout="", stderr="")

    monkeypatch.setenv("VIRTUAL_ENV", "backend/.venv")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert native_command.main(["--", "uv", "sync", "--frozen"]) == 0
    assert seen and seen[0] is not None
    assert "VIRTUAL_ENV" not in seen[0]


def _target(makefile: str, name: str) -> str:
    return makefile.split(f"{name}:", 1)[1].split("\n\n", 1)[0]


def test_make_init_delegates_directly_to_native_uv_and_npm() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    init = _target(makefile, "init")
    assert (
        "uv sync --project backend --locked --group dev --group quality --group build"
        in init
    )
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in init
    assert "release-static-dir" not in init
    assert "transport-clean" not in init
    assert "backend-python-capability-check" not in init
    assert "oh-goon-network-retry" not in init
    assert "frontend_install.sh" not in init


def test_frontend_install_is_native_npm_only() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = _target(makefile, "frontend-install")
    assert "npm ci --prefer-offline --ignore-scripts --no-audit --no-fund" in target
    assert "frontend_install.sh" not in target
    assert not (ROOT / "scripts/frontend_install.sh").exists()


def test_lean_certification_consumes_native_init_without_reacquisition() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    plan = (ROOT / "templates/plans/oh-goon/app-certification/plan.yml").read_text(
        encoding="utf-8"
    )
    compiled = parse_plan_template(plan)
    assert compiled.execution is not None
    assert compiled.execution.purpose is ExecutionPurpose.VERIFICATION
    assert "dependency-materialize" not in plan
    assert "make init" not in plan
    assert not (ROOT / "templates/goons/oh-goon/dependency-materialize").exists()
    assert "certification-dependencies-prepare:" not in makefile
    assert "certification-dependencies-replay:" not in makefile
    assert "oh-goon-network-retry" not in _target(makefile, "init")


def test_native_command_banner_uses_repository_command_banner_owner() -> None:
    source = (
        ROOT / "backend/src/oh_goon/repository_tooling/native_command.py"
    ).read_text(encoding="utf-8")
    assert '_COMMAND_BANNER = _ROOT / "scripts" / "command_banner.sh"' in source
    assert _has_canonical_banner_command(source)
    assert 'command = ("bash", str(_COMMAND_BANNER)' not in source
    assert 'print(f"\\n=== NATIVE TOOL COMMAND' not in source


def test_command_banner_interpreter_has_one_repository_authority() -> None:
    native = (
        ROOT / "backend/src/oh_goon/repository_tooling/native_command.py"
    ).read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    run_step = (ROOT / "scripts/run_step.sh").read_text(encoding="utf-8")
    run_logged = (ROOT / "scripts/run_logged.sh").read_text(encoding="utf-8")

    assert _has_canonical_banner_command(native)
    assert "COMMAND_BANNER = sh scripts/command_banner.sh" in makefile
    assert 'sh scripts/command_banner.sh "$label" "$detail"' in run_step
    assert 'sh "$ROOT/scripts/command_banner.sh" "OH-GOON ${label^^}"' in run_logged
    for source in (native, makefile, run_step, run_logged):
        assert "bash scripts/command_banner.sh" not in source
        assert 'bash "$ROOT/scripts/command_banner.sh"' not in source


def test_command_banner_has_one_tty_no_color_policy_for_stdout_and_stderr() -> None:
    source = (ROOT / "scripts/command_banner.sh").read_text(encoding="utf-8")
    assert '[ -t "$fd" ]' in source
    assert '[ -z "${NO_COLOR:-}" ]' in source
    assert '[ "${TERM:-}" != "dumb" ]' in source


def test_makefile_has_no_parse_time_shell_execution() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    shell_assignments = [line for line in makefile.splitlines() if ":= $(shell" in line]
    assert shell_assignments == []
