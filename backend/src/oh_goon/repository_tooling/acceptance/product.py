from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from oh_goon.core import local_access_protocol
from oh_goon.repository_tooling.release.browser_acceptance import run_runtime_browser_acceptance
from oh_goon.repository_tooling.local_access_http import authorized_request, new_certification_access_token
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.acceptance.shared_plan import exercise_shared_plan

ROOT = Path(__file__).resolve().parents[5]
HOST = "127.0.0.1"


def exercise_product_api(
    *,
    base_url: str,
    acceptance_repository: Path,
    server_repository_path: str,
    access_token: str,
) -> str:
    """Exercise the real Repository -> Goon -> Step -> Game Tape API journey."""

    prepare_acceptance_repository(acceptance_repository)
    return run_acceptance_goon(
        base_url=base_url,
        server_repository_path=server_repository_path,
        access_token=access_token,
    )


def exercise_product_runtime(
    *,
    root: Path,
    base_url: str,
    acceptance_repository: Path,
    server_repository_path: str,
    browser_repository_path: str,
    access_token: str,
) -> str:
    """Exercise one real Repo -> Goon -> Game Tape -> production-browser journey."""

    run_id = exercise_product_api(
        base_url=base_url,
        acceptance_repository=acceptance_repository,
        server_repository_path=server_repository_path,
        access_token=access_token,
    )
    run_runtime_browser_acceptance(
        root=root,
        base_url=base_url,
        repository_path=browser_repository_path,
        run_id=run_id,
        access_token=access_token,
    )
    return run_id


def _get_json(url: str, *, access_token: str) -> object:
    request = authorized_request(url, access_token=access_token)
    with urllib.request.urlopen(request, timeout=5.0) as response:
        return json.loads(response.read().decode("utf-8"))


def exercise_location_lifecycle(
    *,
    base_url: str,
    configured_root: Path,
    run_id: str,
    access_token: str,
) -> None:
    """Prove unavailable current discovery does not rewrite historical execution truth."""

    configured = str(configured_root.resolve())
    initial = _get_json(f"{base_url}/api/locations", access_token=access_token)
    if not isinstance(initial, dict) or configured not in initial.get("locations", []):
        raise RuntimeError(
            "acceptance repository root was not persisted as a configured location"
        )
    moved = configured_root.with_name(f"{configured_root.name}-temporarily-unavailable")
    if moved.exists():
        raise RuntimeError(f"temporary lifecycle path already exists: {moved}")
    initial_repositories = _get_json(
        f"{base_url}/api/repositories", access_token=access_token
    )
    if not isinstance(initial_repositories, list) or not initial_repositories:
        raise RuntimeError(
            "configured location did not expose repositories before lifecycle exercise"
        )
    configured_root.rename(moved)
    try:
        unavailable = _get_json(f"{base_url}/api/locations", access_token=access_token)
        if not isinstance(unavailable, dict) or configured not in unavailable.get(
            "unavailable", []
        ):
            raise RuntimeError(
                "configured location disappearance was not reported as unavailable"
            )
        repositories = _get_json(f"{base_url}/api/repositories", access_token=access_token)
        if repositories != []:
            raise RuntimeError(
                "repositories from an unavailable configured location remained discoverable"
            )
        run = _get_json(f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token)
        if "acceptance-step" not in json.dumps(run):
            raise RuntimeError(
                "historical Game Tape was rewritten when the current repository disappeared"
            )
    finally:
        moved.rename(configured_root)
    restored = _get_json(f"{base_url}/api/locations", access_token=access_token)
    if not isinstance(restored, dict) or restored.get("unavailable") != []:
        raise RuntimeError("restored configured location remained marked unavailable")
    repositories = _get_json(f"{base_url}/api/repositories", access_token=access_token)
    if repositories != initial_repositories:
        raise RuntimeError(
            "repository discovery did not recover the pre-disappearance catalog "
            "after the configured location returned"
        )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def _wait_http(url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _source_runtime_environment(root: Path, state: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("OH_GOON_") or name in {
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "UV_RESOLUTION",
            "UV_PRERELEASE",
            "UV_EXCLUDE_NEWER",
            "UV_PROJECT_ENVIRONMENT",
        }:
            env.pop(name, None)
    env.update(
        {
            "HOME": str(state / "home"),
            "XDG_CONFIG_HOME": str(state / "config"),
            "XDG_DATA_HOME": str(state / "data"),
            "XDG_CACHE_HOME": str(state / "cache"),
            "OH_GOON_DB": str(state / "oh-goon.db"),
            "OH_GOON_LEAGUE_FILE": str(state / "league.yml"),
            "OH_GOON_SCOUT_ENABLED": "1",
        }
    )
    return env


def accept_source_product(
    root: Path = ROOT, *, include_browser: bool = True
) -> tuple[str, ...]:
    """Run the product journey against the real source runtime, not fixture APIs."""

    with tempfile.TemporaryDirectory(prefix="oh-goon-product-accept-") as temporary:
        temp = Path(temporary)
        state = temp / "state"
        repositories = temp / "repositories"
        repository = repositories / "acceptance-repo"
        repositories.mkdir()
        port = _free_port()
        base_url = f"http://{HOST}:{port}"
        access_token = new_certification_access_token()
        env = _source_runtime_environment(root, state)
        env[local_access_protocol.LOCAL_ACCESS_ENV] = access_token
        process = subprocess.Popen(
            (
                "uv",
                "run",
                "--frozen",
                "--no-dev",
                "--project",
                "backend",
                "uvicorn",
                "oh_goon.api.app:app",
                "--host",
                HOST,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ),
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_http(f"{base_url}/healthz")
            run_id = exercise_product_api(
                base_url=base_url,
                acceptance_repository=repository,
                server_repository_path=str(repository.resolve()),
                access_token=access_token,
            )
            exercise_location_lifecycle(
                base_url=base_url,
                configured_root=repositories,
                run_id=run_id,
                access_token=access_token,
            )
            exercise_shared_plan(base_url, repositories, access_token=access_token)
            if not include_browser:
                return ()
            run_runtime_browser_acceptance(
                root=root,
                base_url=base_url,
                repository_path=str(repositories.resolve()),
                run_id=run_id,
                access_token=access_token,
            )
        finally:
            _stop(process)
    return ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oh-goon-product-acceptance")
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="certify real repository/API execution without requiring browser dependencies",
    )
    args = parser.parse_args(argv)
    try:
        issues = accept_source_product(ROOT, include_browser=not args.backend_only)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        issues = (str(exc),)
    if issues:
        print("Product acceptance failed:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    suffix = "Game Tape" if args.backend_only else "Game Tape -> production browser"
    print(f"Product acceptance passed: real Repo -> Goon -> {suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
