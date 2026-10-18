from __future__ import annotations

from collections.abc import Callable
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
from oh_goon.repository_tooling.release.check_release_artifact import release_artifact_issues
from oh_goon.repository_tooling.local_access_http import authorized_request, new_certification_access_token
from oh_goon.repository_tooling.acceptance.product import (
    exercise_location_lifecycle,
    exercise_product_api,
    exercise_product_runtime,
)
from oh_goon.repository_tooling.acceptance.shared_plan import exercise_shared_plan

ROOT = Path(__file__).resolve().parents[5]
HOST = "127.0.0.1"
Runner = Callable[..., subprocess.CompletedProcess[object]]


def _allocate_acceptance_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def _wait_http(
    url: str,
    timeout_seconds: float = 30.0,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> bytes:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process is not None and (returncode := process.poll()) is not None:
            raise RuntimeError(
                f"installed release exited with code {returncode} while waiting for {url}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return response.read()
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for HTTP 200 from {url}")


def _request_json(
    method: str,
    url: str,
    *,
    access_token: str,
    payload: dict[str, object] | None = None,
) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(
        url, access_token=access_token, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=3.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_command(venv: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return venv / ("Scripts" if os.name == "nt" else "bin") / f"{name}{suffix}"


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _installed_runtime_environment(state: Path, *, access_token: str) -> dict[str, str]:
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
            "LOCALAPPDATA": str(state / "local-app-data"),
            "OH_GOON_DB": str(state / "oh-goon.db"),
            "OH_GOON_LEAGUE_FILE": str(state / "league.yml"),
            local_access_protocol.LOCAL_ACCESS_ENV: access_token,
        }
    )
    return env


def _verify_installed_package_origin(
    *,
    command: Path,
    temp: Path,
    env: dict[str, str],
    expected_version: str,
) -> tuple[str, ...]:
    identity_command = command.with_name(
        "oh-goon-runtime-identity.exe" if os.name == "nt" else "oh-goon-runtime-identity"
    )
    result = subprocess.run(
        (str(identity_command),),
        cwd=temp,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != 2:
        return ("installed release provenance probe returned unexpected output",)
    venv = command.parent.parent.resolve()
    module_path = Path(lines[0]).resolve()
    if not module_path.is_relative_to(venv):
        return (
            f"installed release imported oh_goon outside clean venv: {module_path}",
        )
    if lines[1].strip() != expected_version:
        return (
            f"installed release imported version {lines[1].strip()!r}, expected {expected_version!r}",
        )
    return ()


def _accept_installed_runtime(
    root: Path, command: Path, temp: Path, *, port: int
) -> tuple[str, ...]:
    state = temp / "state"
    repositories = temp / "repositories"
    repositories.mkdir()
    acceptance_repository = repositories / "acceptance-repo"
    access_token = new_certification_access_token()
    env = _installed_runtime_environment(state, access_token=access_token)
    provenance_issues = _verify_installed_package_origin(
        command=command,
        temp=temp,
        env=env,
        expected_version=(root / "VERSION").read_text(encoding="utf-8").strip(),
    )
    if provenance_issues:
        return provenance_issues
    base_url = f"http://{HOST}:{port}"
    process = subprocess.Popen(
        (str(command), "serve", "--host", HOST, "--port", str(port)),
        cwd=temp,
        env=env,
    )
    try:
        _wait_http(f"{base_url}/healthz", process=process)
        html = _wait_http(f"{base_url}/")
        if b"<html" not in html.lower() and b"<!doctype html" not in html.lower():
            raise RuntimeError("installed release root did not return bundled HTML")
        _request_json("POST", f"{base_url}/api/locations", access_token=access_token, payload={"path": str(repositories)})
        run_id = exercise_product_runtime(
            root=root,
            base_url=base_url,
            acceptance_repository=acceptance_repository,
            server_repository_path=str(acceptance_repository.resolve()),
            browser_repository_path=str(repositories.resolve()),
            access_token=access_token,
        )
        exercise_location_lifecycle(
            base_url=base_url,
            configured_root=repositories,
            run_id=run_id,
            access_token=access_token,
        )
        exercise_shared_plan(base_url, repositories, access_token=access_token)
    finally:
        _stop_process(process)
    if not (state / "oh-goon.db").is_file():
        return ("installed release did not create persistent SQLite state",)

    process = subprocess.Popen(
        (str(command), "serve", "--host", HOST, "--port", str(port)),
        cwd=temp,
        env=env,
    )
    try:
        _wait_http(f"{base_url}/healthz", process=process)
        locations = _request_json("GET", f"{base_url}/api/locations", access_token=access_token)
        if not isinstance(locations, dict) or str(
            repositories.resolve()
        ) not in locations.get("locations", []):
            return ("installed release did not preserve location state across restart",)
        historical_run = _request_json("GET", f"{base_url}/api/fleet/runs/{run_id}", access_token=access_token)
        if "acceptance-step" not in json.dumps(historical_run):
            return (
                "installed release did not preserve historical Game Tape across restart",
            )
        shared_plan = _request_json(
            "GET",
            f"{base_url}/api/league/shared-plans/acceptance-shared",
            access_token=access_token,
        )
        if (
            not isinstance(shared_plan, dict)
            or shared_plan.get("name") != "acceptance-shared"
        ):
            return (
                "installed release did not preserve Shared Plan state across restart",
            )
        post_restart_run_id = exercise_product_api(
            base_url=base_url,
            acceptance_repository=acceptance_repository,
            server_repository_path=str(acceptance_repository.resolve()),
            access_token=access_token,
        )
        if post_restart_run_id == run_id:
            return ("installed release reused historical Fleet Run identity after restart",)
        history = _request_json(
            "GET", f"{base_url}/api/fleet/runs?limit=20", access_token=access_token
        )
        rendered_history = json.dumps(history)
        if run_id not in rendered_history or post_restart_run_id not in rendered_history:
            return ("installed release history did not retain both pre/post-restart runs",)
    finally:
        _stop_process(process)
    return ()


def _install_locked_runtime(
    root: Path,
    *,
    temp: Path,
    wheel: Path,
    runner: Runner = subprocess.run,
) -> Path:
    """Install the wheel with runtime dependencies exported from the authoritative uv.lock."""

    venv = temp / "venv"
    requirements = temp / "runtime-requirements.txt"
    runner(
        ("uv", "venv", "--python", sys.executable, "--no-python-downloads", str(venv)),
        cwd=root,
        check=True,
    )
    python = _venv_python(venv)
    runner(
        (
            "uv",
            "export",
            "--project",
            str(root / "backend"),
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-python-downloads",
            "--format",
            "requirements.txt",
            "--output-file",
            str(requirements),
        ),
        cwd=root,
        check=True,
    )
    runner(
        (
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--no-python-downloads",
            "-r",
            str(requirements),
        ),
        cwd=root,
        check=True,
    )
    runner(
        (
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            "--no-python-downloads",
            str(wheel),
        ),
        cwd=root,
        check=True,
    )
    return venv


def accept_release(
    root: Path = ROOT, *, runner: Runner = subprocess.run
) -> tuple[str, ...]:
    # build-release owns the one pre-materialization native lock-validity proof.
    # Do not preflight the same uv/npm state again here.
    runner(("make", "build-release"), cwd=root, check=True)
    # Engines come from explicit host setup or the lock-derived acceptance image.
    # This command owns the fixture-backed browser + reviewed visual release matrix.
    runner(("npm", "run", "browser:release"), cwd=root / "frontend", check=True)
    if artifact_issues := release_artifact_issues(root):
        return tuple(f"release artifact: {issue}" for issue in artifact_issues)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    wheel = (
        root / "generated" / "release" / "dist" / f"oh_goon-{version}-py3-none-any.whl"
    )
    if not wheel.is_file():
        return (f"verified release wheel is missing: {wheel.relative_to(root)}",)

    with tempfile.TemporaryDirectory(prefix="oh-goon-release-accept-") as temporary:
        temp = Path(temporary)
        venv = _install_locked_runtime(root, temp=temp, wheel=wheel, runner=runner)
        command = _venv_command(venv, "oh-goon")
        runner(
            (str(command), "--help"), cwd=temp, check=True, stdout=subprocess.DEVNULL
        )
        return _accept_installed_runtime(
            root, command, temp, port=_allocate_acceptance_port()
        )


def main() -> int:
    try:
        issues = accept_release(ROOT)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        issues = (str(exc),)
    if issues:
        print("Real release acceptance failed:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(
        "Real release acceptance passed: locked build -> full browser matrix -> "
        "locked clean install -> UI/API -> persisted restart"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
