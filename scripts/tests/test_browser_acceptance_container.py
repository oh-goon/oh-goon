from __future__ import annotations

from collections.abc import Callable

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.release.browser_acceptance_container import (
    BrowserAcceptanceEnvironmentError,
    locked_playwright_version,
    playwright_image,
    run_browser_acceptance_container,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.process
@pytest.mark.parametrize("failed_owner", [None, "uv", "npm"])
def test_authored_container_bootstrap_runs_native_materialization_and_smoke(
    tmp_path: Path, failed_owner: str | None
) -> None:
    dockerfile = (ROOT / "docker/playwright/Dockerfile").read_text(encoding="utf-8")
    # Execute the authored shell-form bootstrap, leaving target resolution to Make.
    bootstrap_commands = [
        line.removeprefix("RUN ")
        for line in dockerfile.splitlines()
        if line.startswith("RUN make ")
    ]
    assert len(bootstrap_commands) == 1
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "frontend").mkdir()
    shutil.copy2(ROOT / "Makefile", repo / "Makefile")
    for script in ("run_step.sh", "command_banner.sh"):
        shutil.copy2(ROOT / "scripts" / script, repo / "scripts" / script)
    tools = tmp_path / "tools"
    tools.mkdir()
    events = tmp_path / "events"
    for name, script in {
        "uv": """#!/bin/sh
set -eu
[ "$1" = sync ]
printf '%s\\n' backend >> "$BOOTSTRAP_EVENTS"
[ "${FAIL_OWNER:-}" != uv ] || exit 73
mkdir -p backend/.venv/bin
cat > backend/.venv/bin/oh-goon <<'CLI'
#!/bin/sh
set -eu
[ "$1" = --help ]
printf '%s\\n' smoke >> "$BOOTSTRAP_EVENTS"
CLI
chmod +x backend/.venv/bin/oh-goon
""",
        "npm": """#!/bin/sh
set -eu
[ "$1" = ci ]
printf '%s\\n' frontend >> "$BOOTSTRAP_EVENTS"
[ "${FAIL_OWNER:-}" != npm ] || exit 74
mkdir -p node_modules
""",
    }.items():
        executable = tools / name
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "MAKEFLAGS": "",
        "BOOTSTRAP_EVENTS": str(events),
        "FAIL_OWNER": failed_owner or "",
    }
    result = run_command(
        "sh",
        "-eu",
        "-c",
        bootstrap_commands[0],
        cwd=repo,
        env=environment,
        timeout=15,
        max_capture_bytes_per_stream=64 * 1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    assert (result.returncode == 0) is (failed_owner is None), (
        result.stdout + result.stderr
    )
    observed = events.read_text(encoding="utf-8").splitlines()
    expected = ["backend", "frontend", "smoke"]
    if failed_owner is not None:
        expected = expected[: {"uv": 1, "npm": 2}[failed_owner]]
    assert observed == expected


def _write_lock(root: Path) -> None:
    frontend = root / "frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")


def _npm_tree(
    *, test: str = "1.62.1", playwright: str = "1.62.1", core: str = "1.62.1"
) -> str:
    return json.dumps(
        {
            "dependencies": {
                "@playwright/test": {
                    "version": test,
                    "dependencies": {
                        "playwright": {
                            "version": playwright,
                            "dependencies": {"playwright-core": {"version": core}},
                        }
                    },
                }
            }
        }
    )


def _npm_runner(payload: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert command == (
            "npm",
            "ls",
            "--package-lock-only",
            "--json",
            "@playwright/test",
            "playwright",
            "playwright-core",
        )
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    return runner


def test_locked_playwright_version_delegates_selection_to_native_npm(
    tmp_path: Path,
) -> None:
    _write_lock(tmp_path)
    assert (
        locked_playwright_version(tmp_path, runner=_npm_runner(_npm_tree())) == "1.62.1"
    )

    with pytest.raises(
        BrowserAcceptanceEnvironmentError, match="one exact Playwright version"
    ):
        locked_playwright_version(
            tmp_path,
            runner=_npm_runner(_npm_tree(core="1.62.0")),
        )


def test_playwright_image_refuses_unpinned_versions() -> None:
    assert playwright_image("1.62.1") == "mcr.microsoft.com/playwright:v1.62.1-noble"
    with pytest.raises(BrowserAcceptanceEnvironmentError, match="unpinned"):
        playwright_image("latest")


def test_native_npm_is_the_only_lock_interpreter(tmp_path: Path) -> None:
    _write_lock(tmp_path)
    lock = tmp_path / "frontend/package-lock.json"
    lock.write_bytes(b"not a JSON document\n")
    assert (
        locked_playwright_version(tmp_path, runner=_npm_runner(_npm_tree())) == "1.62.1"
    )

    # Even a locally readable selection cannot replace a refused native result.
    lock.write_text(_npm_tree(), encoding="utf-8")

    def refused(
        command: tuple[str, ...], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="native lock refused"
        )

    with pytest.raises(BrowserAcceptanceEnvironmentError, match="native lock refused"):
        locked_playwright_version(tmp_path, runner=refused)


def test_container_runner_uses_locked_image_and_disposable_shm_bounded_run(
    tmp_path: Path,
) -> None:
    _write_lock(tmp_path)
    dockerfile = tmp_path / "docker" / "playwright" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if command[0] == "npm":
            assert kwargs["cwd"] == tmp_path / "frontend"
            assert kwargs["check"] is False
            return subprocess.CompletedProcess(
                command, 0, stdout=_npm_tree(), stderr=""
            )
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is True
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    version = run_browser_acceptance_container(tmp_path, runner=runner)

    assert version == "1.62.1"
    assert commands == [
        (
            "docker",
            "build",
            "--pull",
            "--file",
            str(dockerfile),
            "--build-arg",
            "PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright:v1.62.1-noble",
            "--build-arg",
            "PLAYWRIGHT_VERSION=1.62.1",
            "--tag",
            "oh-goon-browser-acceptance:playwright-1.62.1",
            str(tmp_path),
        ),
        (
            "docker",
            "run",
            "--rm",
            "--init",
            "--shm-size=1g",
            "oh-goon-browser-acceptance:playwright-1.62.1",
        ),
    ]
