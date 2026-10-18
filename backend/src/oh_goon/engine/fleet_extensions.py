from __future__ import annotations

import hashlib
import os
import shutil
import tomllib
from pathlib import Path
from uuid import uuid4

from oh_goon.core.errors import FleetExtensionInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.fleet_plan import ExtensionCatalog, ExtensionDeclaration, ManagedExtension
from oh_goon.core.path_authority import (
    RepositoryPathSpace,
    StrictHostPathAuthority,
    canonical_path,
    ensure_private_directory,
)
from oh_goon.core.path_layouts import ManagedPreparationPathLayout
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import WorktreeState
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.engine.native_git_environment import capture_native_git_authority
from oh_goon.engine.native_uv_environment import NativeUvAuthority, capture_native_uv_authority
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    exact_file_write_lock,
    publish_prepared_directory,
    sync_prepared_path,
)


class FleetExtensionStore:
    """Fleet-owned immutable-by-identity extension checkouts.

    TOML declares *which* source revision and scripts Fleet may use.  This store owns
    acquiring that exact Git revision into the Fleet data directory.  Plan resolution
    is intentionally side-effect free: it may only bind a checkout that was imported
    earlier through ``sync``.
    """

    def __init__(self, root: Path, *, path_authority: StrictHostPathAuthority | None) -> None:
        self._root = canonical_path(root)
        self._path_authority = path_authority
        self._preparation = ManagedPreparationPathLayout.from_root(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def checkout_path(self, extension_id: str) -> Path:
        return self._root / extension_id

    def environment_path(self, extension_id: str) -> Path:
        return self._root / ".environments" / extension_id

    def sync_catalog(
        self,
        catalog: ExtensionCatalog,
        *,
        catalog_root: Path,
    ) -> tuple[ManagedExtension, ...]:
        return tuple(
            self.sync(declaration, catalog_root=catalog_root) for declaration in catalog.extensions
        )

    def sync(
        self,
        declaration: ExtensionDeclaration,
        *,
        catalog_root: Path,
    ) -> ManagedExtension:
        native_uv = capture_native_uv_authority()
        source = _canonical_source(declaration.source, catalog_root)
        extension_id = _extension_id(source, declaration.revision)
        destination = self._root / extension_id
        env_path = self.environment_path(extension_id)
        if destination.exists():
            managed = self._validate_checkout(destination, declaration, source, extension_id)
            _ensure_environment(
                managed, env_path, self._path_authority, self._preparation, native_uv
            )
            return managed

        ensure_private_directory(self._root)
        native_git = capture_native_git_authority()
        transport_environment = native_git.transport_environment()
        temporary = self._root / f".{extension_id}-{uuid4().hex}"
        try:
            clone = _git_service(self._path_authority).run(
                "-c",
                "core.hooksPath=/dev/null",
                "clone",
                "--no-checkout",
                "--template=",
                source,
                str(temporary),
                timeout=300.0,
                env=transport_environment,
            )
            if clone.returncode != 0:
                raise FleetExtensionInvalid(
                    f"Cannot import extension {declaration.name} from {source}: {clone.stderr or clone.stdout}"
                )
            checkout = _git_service(self._path_authority).run(
                "-c",
                "core.hooksPath=/dev/null",
                "checkout",
                "--detach",
                declaration.revision,
                cwd=temporary,
                env=_git_fleet_local_environment(self._preparation),
            )
            if checkout.returncode != 0:
                raise FleetExtensionInvalid(
                    f"Cannot checkout extension {declaration.name} revision {declaration.revision}: "
                    f"{checkout.stderr or checkout.stdout}"
                )
            managed = self._validate_checkout(
                temporary,
                declaration,
                source,
                extension_id,
            )
            sync_prepared_path(temporary)
            try:
                publish_prepared_directory(temporary, destination)
            except ExactPublicationDurabilityError:
                raise
            except OSError:
                if not destination.exists():
                    raise
                # Another process imported the same content-addressed checkout first.
                shutil.rmtree(temporary, ignore_errors=True)
                managed = self._validate_checkout(
                    destination,
                    declaration,
                    source,
                    extension_id,
                )
                _ensure_environment(
                    managed, env_path, self._path_authority, self._preparation, native_uv
                )
                return managed
            managed = managed.model_copy(update={"project_path": destination})
            _ensure_environment(
                managed, env_path, self._path_authority, self._preparation, native_uv
            )
            return managed
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def resolve_cached(
        self,
        declaration: ExtensionDeclaration,
        *,
        catalog_root: Path,
    ) -> ManagedExtension:
        source = _canonical_source(declaration.source, catalog_root)
        extension_id = _extension_id(source, declaration.revision)
        destination = self._root / extension_id
        if not destination.is_dir():
            raise FleetExtensionInvalid(
                f"Extension {declaration.name} revision {declaration.revision} is not imported; "
                "run 'oh-goon plan prepare <plan.yml> --extensions <catalog.toml>' first"
            )
        return self._validate_checkout(destination, declaration, source, extension_id)

    def check_environment(self, extension_id: str, *, timeout: float = 120.0) -> None:
        project_path = self.checkout_path(extension_id)
        environment_path = self.environment_path(extension_id)
        with exact_file_write_lock(environment_path):
            if not environment_path.is_dir():
                raise FleetExtensionInvalid(
                    f"Managed extension runtime is not prepared for {extension_id}; "
                    "run 'oh-goon plan prepare <plan.yml> --extensions <catalog.toml>'"
                )
            native_uv = capture_native_uv_authority()
            result = _check_environment(
                project_path,
                environment_path,
                self._path_authority,
                self._preparation,
                native_uv,
                timeout=timeout,
            )
            if result.returncode != 0:
                raise FleetExtensionInvalid(
                    f"Managed extension runtime is not prepared for {extension_id}: "
                    f"{result.stderr or result.stdout}"
                )

    def _validate_checkout(
        self,
        project_path: Path,
        declaration: ExtensionDeclaration,
        source: str,
        extension_id: str,
    ) -> ManagedExtension:
        observation = _git_service(self._path_authority).observe(
            project_path, env=_git_fleet_local_environment(self._preparation)
        )
        if not observation.is_repository:
            raise FleetExtensionInvalid(
                f"Managed extension checkout is not a Git repository: {project_path}"
            )
        if (
            observation.head_sha is None
            or observation.head_sha.lower() != declaration.revision.lower()
        ):
            raise FleetExtensionInvalid(
                f"Managed extension {declaration.name} is not at admitted revision {declaration.revision}"
            )
        fingerprint = observation.fingerprint
        if observation.worktree_state is not WorktreeState.CLEAN:
            raise FleetExtensionInvalid(
                f"Managed extension {declaration.name} checkout was modified after import"
            )

        pyproject = project_path / "pyproject.toml"
        lockfile = project_path / "uv.lock"
        try:
            pyproject_text = pyproject.read_text(encoding="utf-8")
            payload = tomllib.loads(pyproject_text)
            lock_sha256 = sha256_file(lockfile)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise FleetExtensionInvalid(
                f"Managed extension {declaration.name} requires readable pyproject.toml and uv.lock: {exc}"
            ) from exc

        project = payload.get("project")
        if not isinstance(project, dict):
            raise FleetExtensionInvalid(f"Extension {declaration.name} has no [project] table")
        project_name = project.get("name")
        if not isinstance(project_name, str) or not project_name.strip():
            raise FleetExtensionInvalid(f"Extension {declaration.name} has no valid project.name")
        scripts = project.get("scripts")
        if not isinstance(scripts, dict) or not scripts:
            raise FleetExtensionInvalid(
                f"Extension {declaration.name} has no [project.scripts] table"
            )
        missing = [command for command in declaration.commands if command not in scripts]
        if missing:
            raise FleetExtensionInvalid(
                f"Extension {declaration.name} does not export allowlisted scripts: {', '.join(missing)}"
            )
        if any(
            not isinstance(scripts[command], str) or not scripts[command].strip()
            for command in declaration.commands
        ):
            raise FleetExtensionInvalid(
                f"Extension {declaration.name} has an invalid [project.scripts] entry"
            )

        return ManagedExtension(
            extension_id=extension_id,
            source=source,
            revision=declaration.revision.lower(),
            project_name=project_name,
            project_path=project_path,
            project_fingerprint=fingerprint.state_id,
            pyproject_sha256=hashlib.sha256(pyproject_text.encode("utf-8")).hexdigest(),
            uv_lock_sha256=lock_sha256,
            scripts=tuple(sorted(str(name) for name in scripts)),
        )


def _ensure_environment(
    managed: ManagedExtension,
    environment: Path,
    path_authority: StrictHostPathAuthority | None,
    preparation: ManagedPreparationPathLayout,
    native_uv: NativeUvAuthority,
) -> None:
    with exact_file_write_lock(environment):
        if environment.is_dir():
            check = _check_environment(
                managed.project_path,
                environment,
                path_authority,
                preparation,
                native_uv,
                timeout=120.0,
            )
            if check.returncode == 0:
                return
        result = _sync_environment(
            managed.project_path, environment, path_authority, preparation, native_uv
        )
        if result.returncode != 0:
            raise FleetExtensionInvalid(
                f"Cannot prepare extension runtime for {managed.project_name}: "
                f"{result.stderr or result.stdout}"
            )


def _check_environment(
    project: Path,
    environment: Path,
    path_authority: StrictHostPathAuthority | None,
    preparation: ManagedPreparationPathLayout,
    native_uv: NativeUvAuthority,
    *,
    timeout: float,
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
        timeout=timeout,
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


def _git_service(path_authority: StrictHostPathAuthority | None) -> GitService:
    if path_authority is None:
        raise FleetExtensionInvalid(
            "Fleet extension operations require deployment path authority for host tool 'git'"
        )
    return GitService(executable=str(path_authority.tool("git")))


def _uv_executable(path_authority: StrictHostPathAuthority | None) -> str:
    if path_authority is None:
        raise FleetExtensionInvalid(
            "Fleet extension operations require deployment path authority for host tool 'uv'"
        )
    return str(path_authority.tool("uv"))


def _base_preparation_environment(
    preparation: ManagedPreparationPathLayout,
) -> dict[str, str]:
    preparation.ensure()
    return {
        "PATH": os.defpath,
        "HOME": str(preparation.home),
        "USERPROFILE": str(preparation.home),
        "TMPDIR": str(preparation.tmp),
        "TMP": str(preparation.tmp),
        "TEMP": str(preparation.tmp),
        "XDG_CACHE_HOME": str(preparation.cache),
        "UV_CACHE_DIR": str(preparation.cache / "uv"),
        "XDG_CONFIG_HOME": str(preparation.config),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _git_fleet_local_environment(preparation: ManagedPreparationPathLayout) -> dict[str, str]:
    env = _base_preparation_environment(preparation)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _canonical_source(source: str, catalog_root: Path) -> str:
    source = source.strip()
    if not source:
        raise FleetExtensionInvalid("Extension source cannot be empty")
    if "://" in source or source.startswith("git@"):
        return source
    return str(
        RepositoryPathSpace.from_root(catalog_root).resolve(
            source, label="local extension source", kind="dir", require_exists=True
        )
    )


def _extension_id(source: str, revision: str) -> str:
    return stable_id(
        "fleet-extension",
        {"source": source, "revision": revision.lower()},
    )
