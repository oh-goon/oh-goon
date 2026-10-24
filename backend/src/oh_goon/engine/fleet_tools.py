from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.fleet_plan import ManagedToolRuntime
from oh_goon.core.path_authority import (
    StrictHostPathAuthority,
    canonical_path,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import ManagedPreparationPathLayout
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import run_command
from oh_goon.engine.native_uv_environment import NativeUvAuthority, capture_native_uv_authority
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    exact_file_write_lock,
    publish_prepared_directory,
    sync_prepared_path,
)


class FleetToolStore:
    """Fleet-owned prepared runtimes for pinned built-in plan tools.

    Tool dependency resolution is an explicit preparation effect. Repository execution
    only invokes an already-prepared executable and therefore never downloads or resolves
    the tool after EFFECT_STARTED.
    """

    def __init__(self, root: Path, *, path_authority: StrictHostPathAuthority | None) -> None:
        self._root = canonical_path(root)
        self._path_authority = path_authority
        self._preparation = ManagedPreparationPathLayout.from_root(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def project_path(self, runtime_id: str) -> Path:
        return self._root / runtime_id

    def environment_path(self, runtime_id: str) -> Path:
        return self._root / ".environments" / runtime_id

    def prepare_pre_commit(self, requirement: str) -> ManagedToolRuntime:
        native_uv = capture_native_uv_authority()
        runtime_id = _runtime_id("pre-commit", requirement)
        project = self.project_path(runtime_id)
        if not project.is_dir():
            self._create_project(project, runtime_id, requirement, native_uv)
        self._ensure_environment(project, runtime_id, native_uv)
        return self.resolve_prepared_pre_commit(requirement)

    def resolve_prepared_pre_commit(self, requirement: str) -> ManagedToolRuntime:
        runtime_id = _runtime_id("pre-commit", requirement)
        project = self.project_path(runtime_id)
        pyproject = project / "pyproject.toml"
        lockfile = project / "uv.lock"
        try:
            pyproject_bytes = pyproject.read_bytes()
            lock_sha256 = sha256_file(lockfile)
        except OSError as exc:
            raise FleetPlanInvalid(
                f"Pinned pre-commit runtime is not prepared; prepare the original managed runtime "
                f"through FleetPlanService.prepare before locking: {exc}"
            ) from exc
        expected = _project_text(requirement).encode("utf-8")
        if pyproject_bytes != expected:
            raise FleetPlanInvalid(
                "Prepared pre-commit runtime metadata changed; prepare the original managed runtime through FleetPlanService.prepare before locking"
            )
        executable = self.command_path(runtime_id, "pre-commit")
        if not executable.is_file():
            raise FleetPlanInvalid(
                "Prepared pre-commit executable is missing; prepare the original managed runtime through FleetPlanService.prepare before locking"
            )
        return ManagedToolRuntime(
            runtime_id=runtime_id,
            tool="pre-commit",
            requirement=requirement,
            pyproject_sha256=hashlib.sha256(pyproject_bytes).hexdigest(),
            uv_lock_sha256=lock_sha256,
        )

    def check_pre_commit(
        self,
        *,
        runtime_id: str,
        requirement: str,
        pyproject_sha256: str,
        uv_lock_sha256: str,
    ) -> Path:
        if runtime_id != _runtime_id("pre-commit", requirement):
            raise FleetPlanInvalid("Resolved pre-commit runtime identity is invalid")
        project = self.project_path(runtime_id)
        try:
            pyproject_bytes = (project / "pyproject.toml").read_bytes()
            lock_sha256 = sha256_file(project / "uv.lock")
        except OSError as exc:
            raise FleetPlanInvalid(f"Prepared pre-commit runtime disappeared: {exc}") from exc
        if hashlib.sha256(pyproject_bytes).hexdigest() != pyproject_sha256:
            raise FleetPlanInvalid("Prepared pre-commit runtime metadata changed after admission")
        if lock_sha256 != uv_lock_sha256:
            raise FleetPlanInvalid("Prepared pre-commit lock changed after admission")
        environment = self.environment_path(runtime_id)
        with exact_file_write_lock(environment):
            native_uv = capture_native_uv_authority()
            check = _check_environment(
                project, environment, self._path_authority, self._preparation, native_uv
            )
            if check.returncode != 0:
                raise FleetPlanInvalid(
                    "Prepared pre-commit runtime is stale or incomplete; prepare the original managed runtime through FleetPlanService.prepare before locking"
                )
            executable = self.command_path(runtime_id, "pre-commit")
            if not executable.is_file():
                raise FleetPlanInvalid("Prepared pre-commit executable disappeared after admission")
            return executable

    def command_path(self, runtime_id: str, command: str) -> Path:
        environment = self.environment_path(runtime_id)
        return (
            environment
            / ("Scripts" if os.name == "nt" else "bin")
            / (f"{command}.exe" if os.name == "nt" else command)
        )

    def _create_project(
        self,
        project: Path,
        runtime_id: str,
        requirement: str,
        native_uv: NativeUvAuthority,
    ) -> None:
        ensure_private_directory(self._root)
        temporary = self._root / f".{runtime_id}-{uuid4().hex}"
        try:
            temporary.mkdir(mode=0o700)
            (temporary / "pyproject.toml").write_text(_project_text(requirement), encoding="utf-8")
            result = run_command(
                _uv_executable(self._path_authority),
                "lock",
                "--project",
                str(temporary),
                cwd=temporary,
                timeout=300.0,
                env=native_uv.environment(
                    self._preparation,
                    environment=self.environment_path(runtime_id),
                ),
            )
            if result.returncode != 0:
                raise FleetPlanInvalid(
                    f"Cannot lock pinned pre-commit runtime {requirement}: {result.stderr or result.stdout}"
                )
            sync_prepared_path(temporary)
            try:
                publish_prepared_directory(temporary, project)
            except ExactPublicationDurabilityError:
                raise
            except OSError:
                if not project.is_dir():
                    raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _ensure_environment(
        self, project: Path, runtime_id: str, native_uv: NativeUvAuthority
    ) -> None:
        environment = self.environment_path(runtime_id)
        with exact_file_write_lock(environment):
            if environment.is_dir():
                check = _check_environment(
                    project, environment, self._path_authority, self._preparation, native_uv
                )
                if check.returncode == 0 and self.command_path(runtime_id, "pre-commit").is_file():
                    return
            result = _sync_environment(
                project, environment, self._path_authority, self._preparation, native_uv
            )
            if result.returncode != 0:
                raise FleetPlanInvalid(
                    f"Cannot prepare pinned pre-commit runtime: {result.stderr or result.stdout}"
                )


def _check_environment(
    project: Path,
    environment: Path,
    path_authority: StrictHostPathAuthority | None,
    preparation: ManagedPreparationPathLayout,
    native_uv: NativeUvAuthority,
):
    return run_command(
        _uv_executable(path_authority),
        "sync",
        "--project",
        str(project),
        "--frozen",
        "--no-editable",
        "--check",
        cwd=project,
        timeout=120.0,
        env=native_uv.environment(preparation, environment=environment),
    )


def _sync_environment(
    project: Path,
    environment: Path,
    path_authority: StrictHostPathAuthority | None,
    preparation: ManagedPreparationPathLayout,
    native_uv: NativeUvAuthority,
):
    return run_command(
        _uv_executable(path_authority),
        "sync",
        "--project",
        str(project),
        "--frozen",
        "--no-editable",
        cwd=project,
        timeout=900.0,
        env=native_uv.environment(preparation, environment=environment),
    )


def _uv_executable(path_authority: StrictHostPathAuthority | None) -> str:
    if path_authority is None:
        raise FleetPlanInvalid(
            "Fleet managed tool operations require deployment path authority for host tool 'uv'"
        )
    return str(path_authority.tool("uv"))


def _runtime_id(tool: str, requirement: str) -> str:
    return stable_id(
        "fleet-tool-runtime",
        {"tool": tool, "requirement": requirement},
    )


def _project_text(requirement: str) -> str:
    return (
        "[project]\n"
        'name = "oh-goon-pre-commit-runtime"\n'
        'version = "0"\n'
        f'dependencies = ["{requirement}"]\n'
    )
