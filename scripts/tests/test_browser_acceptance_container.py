from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from oh_goon.repository_tooling.release.browser_acceptance_container import (
    BrowserAcceptanceEnvironmentError,
    locked_playwright_version,
    playwright_image,
    run_browser_acceptance_container,
)


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


def _npm_runner(payload: str):
    def runner(
        command: tuple[str, ...], **kwargs
    ) -> subprocess.CompletedProcess[object]:
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


def test_container_runner_uses_locked_image_and_disposable_shm_bounded_run(
    tmp_path: Path,
) -> None:
    _write_lock(tmp_path)
    dockerfile = tmp_path / "docker" / "playwright" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...], **kwargs
    ) -> subprocess.CompletedProcess[object]:
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
