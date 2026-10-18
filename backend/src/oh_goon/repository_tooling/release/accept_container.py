from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from oh_goon.core import local_access_protocol
from oh_goon.repository_tooling.acceptance.product import exercise_product_runtime
from oh_goon.repository_tooling.local_access_http import (
    authorized_request,
    new_certification_access_token,
)
from oh_goon.repository_tooling.public_dependency_privacy import public_source_privacy_issues

ROOT = Path(__file__).resolve().parents[5]
HOST = "127.0.0.1"
_CONTAINER_DEPENDENCY_SOURCE_FILES = (
    Path("backend/pyproject.toml"),
    Path("backend/uv.lock"),
    Path("frontend/package.json"),
    Path("frontend/package-lock.json"),
)


def _allocate_acceptance_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def _wait_http(url: str, timeout_seconds: float = 60.0) -> object:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status < 400:
                    return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}")


def _cleanup_compose(compose: tuple[str, ...], *, root: Path, env: dict[str, str]) -> None:
    subprocess.run(
        (*compose, "down", "--remove-orphans", "--rmi", "local"),
        cwd=root,
        env=env,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _verify_container_package_origin(
    compose: tuple[str, ...], *, root: Path, env: dict[str, str]
) -> tuple[str, ...]:
    result = subprocess.run(
        (
            *compose,
            "exec",
            "-T",
            "oh-goon",
            "oh-goon-runtime-identity",
        ),
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    if len(lines) != 2 or "/site-packages/oh_goon/" not in lines[0].replace("\\", "/"):
        return ("container did not import Oh Goon from the installed wheel",)
    expected = (root / "VERSION").read_text(encoding="utf-8").strip()
    if lines[1].strip() != expected:
        return (f"container imported version {lines[1].strip()!r}, expected {expected!r}",)
    return ()


def _request_json(
    method: str,
    url: str,
    *,
    access_token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else None
    request = authorized_request(
        url, access_token=access_token, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=3.0) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return value


def _acceptance_environment(
    *,
    data: Path,
    repositories: Path,
    port: str,
    dockerfile: str,
    access_token: str,
) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        env.pop(name, None)
    env.update(
        {
            "OH_GOON_DATA_DIR": str(data),
            "OH_GOON_REPOSITORIES_DIR": str(repositories),
            "OH_GOON_PORT": port,
            "OH_GOON_UID": str(os.getuid()) if hasattr(os, "getuid") else "1000",
            "OH_GOON_GID": str(os.getgid()) if hasattr(os, "getgid") else "1000",
            "OH_GOON_DOCKERFILE": dockerfile,
            local_access_protocol.LOCAL_ACCESS_ENV: access_token,
        }
    )
    return env


def _validated_compose_file(root: Path) -> Path:
    path = root / "docker-compose.yml"
    if path.is_symlink() or not path.is_file():
        raise ValueError("container acceptance compose file must be a regular non-symlink file")
    return path


def _validated_dockerfile(root: Path, value: str) -> str:
    path = root / value
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"container acceptance Dockerfile must be a regular non-symlink file: {value}"
        )
    try:
        relative = path.resolve().relative_to((root / "docker").resolve())
    except ValueError as exc:
        raise ValueError("container acceptance Dockerfile must remain under docker/") from exc
    return f"docker/{relative.as_posix()}"


def _container_source_policy_issues(root: Path) -> tuple[str, ...]:
    """Apply repository-owned source/privacy policy without re-deciding native locks."""

    root = root.resolve()
    issues: list[str] = []
    for relative in _CONTAINER_DEPENDENCY_SOURCE_FILES:
        path = root / relative
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            issues.append(f"{relative.as_posix()} must remain inside the checkout")
            continue
        if path.is_symlink() or not path.is_file():
            issues.append(f"{relative.as_posix()} must be a contained regular non-symlink file")
    issues.extend(public_source_privacy_issues(root))
    return tuple(dict.fromkeys(issues))


def accept_container(
    root: Path = ROOT, *, dockerfile: str = "docker/ubuntu/Dockerfile"
) -> tuple[str, ...]:
    issues = list(_container_source_policy_issues(root))
    if issues:
        return tuple(issues)
    try:
        compose_file = _validated_compose_file(root)
        dockerfile = _validated_dockerfile(root, dockerfile)
    except ValueError as exc:
        return (str(exc),)
    with tempfile.TemporaryDirectory(prefix="oh-goon-container-accept-") as temporary:
        temp = Path(temporary)
        data = temp / "data"
        repositories = temp / "repositories"
        data.mkdir()
        repositories.mkdir()
        acceptance_repository = repositories / "acceptance-repo"
        port = str(_allocate_acceptance_port())
        access_token = new_certification_access_token()
        env = _acceptance_environment(
            data=data,
            repositories=repositories,
            port=port,
            dockerfile=dockerfile,
            access_token=access_token,
        )
        base = f"http://{HOST}:{port}"
        project_name = f"oh-goon-accept-{uuid.uuid4().hex[:12]}"
        compose = (
            "docker",
            "compose",
            "--project-name",
            project_name,
            "--file",
            str(compose_file),
        )
        try:
            subprocess.run((*compose, "build", "oh-goon"), cwd=root, env=env, check=True)
            subprocess.run((*compose, "up", "-d", "oh-goon"), cwd=root, env=env, check=True)
            _wait_http(f"{base}/healthz")
            if provenance_issues := _verify_container_package_origin(compose, root=root, env=env):
                return provenance_issues
            subprocess.run(
                ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
                cwd=root / "frontend",
                check=True,
            )
            subprocess.run(("npm", "run", "browser:install"), cwd=root / "frontend", check=True)
            _request_json(
                "POST",
                f"{base}/api/locations",
                access_token=access_token,
                payload={"path": "/repositories"},
            )
            run_id = exercise_product_runtime(
                root=root,
                base_url=base,
                acceptance_repository=acceptance_repository,
                server_repository_path="/repositories/acceptance-repo",
                browser_repository_path="/repositories",
                access_token=access_token,
            )
            subprocess.run((*compose, "down"), cwd=root, env=env, check=True)
            if not (data / "oh-goon.db").is_file():
                return ("container did not persist SQLite database on the host",)
            subprocess.run((*compose, "up", "-d", "oh-goon"), cwd=root, env=env, check=True)
            _wait_http(f"{base}/healthz")
            locations = _request_json("GET", f"{base}/api/locations", access_token=access_token)
            if "/repositories" not in locations.get("locations", []):  # type: ignore[union-attr]
                return ("replacement container did not restore persisted League configuration",)
            persisted_run = _request_json(
                "GET", f"{base}/api/fleet/runs/{run_id}", access_token=access_token
            )
            if persisted_run.get("fleet_run_id") != run_id:  # type: ignore[union-attr]
                return ("replacement container did not restore persisted SQLite Game Tape state",)
        finally:
            _cleanup_compose(compose, root=root, env=env)
    return ()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dockerfile", default="docker/ubuntu/Dockerfile")
    args = parser.parse_args()
    try:
        issues = accept_container(ROOT, dockerfile=args.dockerfile)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        issues = (str(exc),)
    if issues:
        print("Container acceptance failed:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(
        f"Container acceptance passed for {args.dockerfile}: build -> health -> persist -> replace -> restore"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
