"""Native uv owner for one selected locked project (offline).

This observes uv's authority; it never decides dependency graph semantics or
modifies the lock. It is shared by the installed issuer and tooling adapter.
Native metadata checks can execute project build hooks, so callers must retain
the selected code-trust boundary rather than treating this as pure inspection.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oh_goon.core.models.fleet_plan_requirements import UV_PROJECT_SELECTION_SWITCHES

Runner = Callable[..., subprocess.CompletedProcess[str]]
_MAX_CAPTURED_INPUT_BYTES = 8 * 1024 * 1024
_MAX_EXPORT_BYTES = 2 * 1024 * 1024
_PACKAGE_NAME = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")


def native_uv_locked_project_issue(project: Path, *, runner: Runner = subprocess.run) -> str | None:
    if not isinstance(project, Path) or not project.is_absolute() or project.is_symlink():
        return "canonical native uv project directory is required"
    try:
        result = runner(
            ("uv", "lock", "--project", str(project), "--check", "--offline"),
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            env={
                name: value
                for name, value in os.environ.items()
                if name not in UV_PROJECT_SELECTION_SWITCHES
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"native uv lock --check --offline could not run: {exc}"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "native uv lock --check --offline failed").strip()
    return f"native uv lock --check --offline failed: {detail}"


def _export_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("UV_", "PYTHON")) and name not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
    }
    if "UV_CACHE_DIR" in os.environ:
        environment["UV_CACHE_DIR"] = os.environ["UV_CACHE_DIR"]
    return environment


def _uv_version(executable: str, root: Path, environment: dict[str, str]) -> str:
    from oh_goon.engine.command import run_command

    result = run_command(
        executable,
        "--version",
        cwd=root,
        env=environment,
        timeout=10,
        max_capture_bytes_per_stream=4096,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode or result.stdout_truncated or not result.stdout.startswith("uv "):
        raise ValueError("Native uv export version provenance is unavailable")
    return result.stdout.strip()


def _native_artifact_export(
    project: Path,
    *,
    executable: str,
    manifest: bytes,
    lock: bytes,
    excluded_packages: tuple[str, ...],
    environment: dict[str, str],
) -> str:
    from oh_goon.engine.command import run_command

    project.mkdir()
    (project / "pyproject.toml").write_bytes(manifest)
    (project / "uv.lock").write_bytes(lock)
    result = run_command(
        executable,
        "export",
        "--project",
        str(project),
        "--frozen",
        "--offline",
        "--no-config",
        "--no-python-downloads",
        "--format",
        "pylock.toml",
        "--all-groups",
        "--all-extras",
        "--no-emit-workspace",
        "--no-header",
        "--no-annotate",
        *(argument for name in excluded_packages for argument in ("--no-emit-package", name)),
        cwd=project,
        env=environment,
        timeout=30,
        max_capture_bytes_per_stream=_MAX_EXPORT_BYTES,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode:
        raise ValueError(f"Native uv artifact export failed: {result.stderr.strip()[:2048]}")
    if (
        result.stdout_truncated
        or not result.stdout.strip()
        or len(result.stdout.encode("utf-8")) > _MAX_EXPORT_BYTES
    ):
        raise ValueError("Native uv artifact export is unavailable or exceeds its byte bound")
    if (project / "pyproject.toml").read_bytes() != manifest or (
        project / "uv.lock"
    ).read_bytes() != lock:
        raise ValueError("Native uv artifact export changed its captured dependency inputs")
    return result.stdout


def compare_native_uv_dependency_artifacts(
    *,
    before_manifest: bytes,
    before_lock: bytes,
    after_manifest: bytes,
    after_lock: bytes,
    excluded_packages: tuple[str, ...],
) -> dict[str, Any]:
    """Compare native universal artifact exports without interpreting either lock.

    Frozen offline export neither resolves nor installs dependencies. uv owns
    package exclusion and standardized serialization, including sdist/wheel URLs
    and hashes. No-emit preserves transitive packages; pruning would remove them.
    Captures are bounded to 2 MiB each (4 MiB combined), and no project build
    source or hook is materialized. The caller decides admission from these facts.
    """
    if any(
        not isinstance(value, bytes) or not value or len(value) > _MAX_CAPTURED_INPUT_BYTES
        for value in (before_manifest, before_lock, after_manifest, after_lock)
    ):
        raise ValueError("Native uv artifact comparison requires bounded captured input bytes")
    if (
        not isinstance(excluded_packages, tuple)
        or len(excluded_packages) > 64
        or any(
            not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name)
            for name in excluded_packages
        )
        or len(set(excluded_packages)) != len(excluded_packages)
    ):
        raise ValueError("Native uv export exclusions must be unique explicit package names")
    environment = _export_environment()
    executable = shutil.which("uv", path=environment.get("PATH"))
    if executable is None:
        raise ValueError("Native uv artifact exporter is unavailable")
    executable = str(Path(executable).absolute())
    try:
        with tempfile.TemporaryDirectory(prefix="oh-goon-uv-artifacts-") as directory:
            root = Path(directory)
            version = _uv_version(executable, root, environment)
            before = _native_artifact_export(
                root / "before",
                executable=executable,
                manifest=before_manifest,
                lock=before_lock,
                excluded_packages=excluded_packages,
                environment=environment,
            )
            after = _native_artifact_export(
                root / "after",
                executable=executable,
                manifest=after_manifest,
                lock=after_lock,
                excluded_packages=excluded_packages,
                environment=environment,
            )
            if _uv_version(executable, root, environment) != version:
                raise ValueError("Native uv exporter changed during artifact comparison")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Native uv artifact comparison is unavailable: {exc}") from exc
    return {
        "schema": "oh-goon.native-uv-artifact-comparison/v1",
        "authority": "native-uv-export",
        "equivalent": before == after,
        "before_export": before,
        "after_export": after,
        "excluded_packages": list(excluded_packages),
        "scope": {
            "format": "pylock.toml",
            "groups": "all",
            "extras": "all",
            "workspace": "not-emitted",
            "exclusion": "native-no-emit-package",
        },
        "tool": {"name": "uv", "executable": executable, "version": version},
    }
