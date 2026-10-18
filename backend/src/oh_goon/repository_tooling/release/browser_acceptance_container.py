from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[5]
PLAYWRIGHT_PACKAGES = (
    "node_modules/@playwright/test",
    "node_modules/playwright",
    "node_modules/playwright-core",
)


Runner = Callable[..., subprocess.CompletedProcess[object]]


class BrowserAcceptanceEnvironmentError(ValueError):
    pass


def _collect_npm_versions(node: object, versions: dict[str, set[str]]) -> None:
    if not isinstance(node, dict):
        return
    dependencies = node.get("dependencies")
    if not isinstance(dependencies, dict):
        return
    for name, entry in dependencies.items():
        if name in versions and isinstance(entry, dict):
            version = entry.get("version")
            if isinstance(version, str):
                versions[name].add(version)
        _collect_npm_versions(entry, versions)


def locked_playwright_version(
    root: Path = ROOT, *, runner: Runner = subprocess.run
) -> str:
    lock_path = root / "frontend" / "package-lock.json"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise BrowserAcceptanceEnvironmentError(
            "frontend/package-lock.json must be a regular authoritative lockfile"
        )
    try:
        result = runner(
            (
                "npm",
                "ls",
                "--package-lock-only",
                "--json",
                "@playwright/test",
                "playwright",
                "playwright-core",
            ),
            cwd=root / "frontend",
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserAcceptanceEnvironmentError(
            f"npm could not inspect the authoritative Playwright lock selection: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "npm ls failed").strip()
        raise BrowserAcceptanceEnvironmentError(
            f"npm could not inspect the authoritative Playwright lock selection: {detail}"
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise BrowserAcceptanceEnvironmentError(
            "npm ls did not return readable JSON for the Playwright lock selection"
        ) from exc
    versions = {name: set() for name in ("@playwright/test", "playwright", "playwright-core")}
    _collect_npm_versions(payload, versions)
    if any(not values for values in versions.values()):
        raise BrowserAcceptanceEnvironmentError(
            "npm lock selection must contain @playwright/test, playwright and playwright-core"
        )
    selected = set().union(*versions.values())
    if any(len(values) != 1 for values in versions.values()) or len(selected) != 1:
        raise BrowserAcceptanceEnvironmentError(
            "npm lock selection must use one exact Playwright version"
        )
    version = selected.pop()
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise BrowserAcceptanceEnvironmentError(
            "locked Playwright version must be an exact semantic version"
        )
    return version


def playwright_image(version: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise BrowserAcceptanceEnvironmentError("refusing an unpinned Playwright image")
    return f"mcr.microsoft.com/playwright:v{version}-noble"


def run_browser_acceptance_container(
    root: Path = ROOT,
    *,
    runner: Runner = subprocess.run,
    docker_command: str = "docker",
) -> str:
    version = locked_playwright_version(root, runner=runner)
    upstream_image = playwright_image(version)
    local_image = f"oh-goon-browser-acceptance:playwright-{version}"
    runner(
        (
            docker_command,
            "build",
            "--pull",
            "--file",
            str(root / "docker" / "playwright" / "Dockerfile"),
            "--build-arg",
            f"PLAYWRIGHT_IMAGE={upstream_image}",
            "--build-arg",
            f"PLAYWRIGHT_VERSION={version}",
            "--tag",
            local_image,
            str(root),
        ),
        cwd=root,
        check=True,
    )
    runner(
        (
            docker_command,
            "run",
            "--rm",
            "--init",
            "--shm-size=1g",
            local_image,
        ),
        cwd=root,
        check=True,
    )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run release acceptance in the exact locked Playwright image."
    )
    parser.add_argument("action", choices=("version", "run"), nargs="?", default="run")
    args = parser.parse_args()
    try:
        if args.action == "version":
            print(locked_playwright_version())
            return 0
        version = run_browser_acceptance_container()
    except (
        BrowserAcceptanceEnvironmentError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Browser acceptance environment failed: {exc}")
        return 1
    print(f"Browser acceptance passed in locked Playwright {version} environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
