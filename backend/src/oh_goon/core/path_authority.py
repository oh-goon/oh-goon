from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from oh_goon.core._path_validation import PathKind, require_path_kind
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.repository_isolation import (
    require_disjoint_repository_roots,
    require_isolated_repository_tree,
    require_owned_filesystem_tree,
)


@dataclass(frozen=True, slots=True)
class ContainedPathSpace:
    """Canonical boundary for source navigation inside one declared filesystem root.

    Unlike execution paths, source-to-source references may use ``..`` to reach a
    sibling module, but only when the resolved target remains inside this exact root.
    Absolute paths, platform-dependent separators, and non-canonical aliases remain
    invalid.
    """

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> ContainedPathSpace:
        return cls(root=canonical_path(root))

    def resolve_from(
        self,
        base: Path,
        value: str,
        *,
        label: str,
        kind: PathKind = "any",
        require_exists: bool = False,
    ) -> Path:
        base_path = canonical_path(base)
        self.relative(base_path, label=f"{label} base")
        relative = contained_relative_parts(value, label=label)
        candidate = base_path.joinpath(*relative.parts).resolve()
        self.relative(candidate, label=label)
        require_path_kind(candidate, label=label, kind=kind, require_exists=require_exists)
        return candidate

    def relative(self, path: Path, *, label: str) -> PurePosixPath:
        candidate = canonical_path(path)
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise FleetPlanInvalid(f"{label} escapes declared path root: {candidate}")
        return PurePosixPath(*candidate.relative_to(self.root).parts)


@dataclass(frozen=True, slots=True)
class RepositoryPathSpace:
    """Canonical filesystem boundary for one repository-owned execution tree."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> RepositoryPathSpace:
        return cls(root=canonical_path(root))

    def resolve(
        self,
        value: str,
        *,
        label: str,
        kind: PathKind = "any",
        require_exists: bool = False,
    ) -> Path:
        relative = repository_relative_parts(value, label=label)
        candidate = self.root.joinpath(*relative.parts).resolve()
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise FleetPlanInvalid(f"{label} escapes repository boundary: {value}")
        require_path_kind(candidate, label=label, kind=kind, require_exists=require_exists)
        return candidate

    def relative(self, path: Path, *, label: str) -> PurePosixPath:
        candidate = canonical_path(path)
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise FleetPlanInvalid(f"{label} is outside repository boundary: {candidate}")
        relative = candidate.relative_to(self.root)
        return PurePosixPath(*relative.parts)

    @staticmethod
    def require_disjoint_sources(repository_roots: tuple[Path, ...]) -> tuple[Path, ...]:
        """Resolve one selected repository set and prove every peer pair is disjoint."""

        return require_disjoint_repository_roots(repository_roots)

    def require_isolated_source(
        self,
        *,
        other_repository_roots: Iterable[Path] = (),
    ) -> None:
        """Prove this repository tree cannot expose bytes owned by another repository.

        Strict CI/CD deliberately rejects ambiguous filesystem topology. A path being
        lexically/canonically inside ``root`` is not enough: bind mounts, nested Git
        repositories, and external hard links can all make foreign bytes appear under
        an apparently contained path.
        """

        require_isolated_repository_tree(self.root, other_repository_roots=other_repository_roots)

    def require_owned_tree(self) -> None:
        """Prove a Fleet-private tree has no live filesystem edge outside itself."""

        require_owned_filesystem_tree(self.root)


@dataclass(frozen=True, slots=True)
class AttemptWorkspaceAuthority:
    """Prove the complete private filesystem authority for one physical Fleet attempt."""

    root: Path
    workspace: Path
    home: Path
    tmp: Path
    cache: Path
    config: Path
    etc: Path

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        workspace: Path,
        home: Path,
        tmp: Path,
        cache: Path,
        config: Path,
        etc: Path,
    ) -> "AttemptWorkspaceAuthority":
        authority = cls(
            root=canonical_path(root),
            workspace=canonical_path(workspace),
            home=canonical_path(home),
            tmp=canonical_path(tmp),
            cache=canonical_path(cache),
            config=canonical_path(config),
            etc=canonical_path(etc),
        )
        authority.prove()
        return authority

    def prove(self) -> None:
        space = RepositoryPathSpace.from_root(self.root)
        children = {
            "workspace": self.workspace,
            "home": self.home,
            "tmp": self.tmp,
            "cache": self.cache,
            "config": self.config,
            "etc": self.etc,
        }
        if len(set(children.values())) != len(children):
            raise FleetPlanInvalid("Fleet attempt private roots must be distinct")
        for name, path in children.items():
            relative = space.relative(path, label=f"Fleet attempt {name} root")
            if relative != PurePosixPath(name):
                raise FleetPlanInvalid(
                    f"Fleet attempt {name} root is not the canonical owned child: {path}"
                )
            if path.is_symlink():
                raise FleetPlanInvalid(f"Fleet attempt {name} root must not be a symlink: {path}")
        space.require_owned_tree()


@dataclass(frozen=True, slots=True)
class RepositoryScopedPath:
    """One path whose filesystem authority and repository owner are bound together."""

    owner_repository_id: str
    root: Path
    path: Path

    @classmethod
    def create(
        cls,
        *,
        owner_repository_id: str,
        root: Path,
        path: Path,
        label: str,
    ) -> RepositoryScopedPath:
        if not owner_repository_id:
            raise FleetPlanInvalid(f"{label} requires a repository owner")
        space = RepositoryPathSpace.from_root(root)
        candidate = canonical_path(path)
        space.relative(candidate, label=label)
        return cls(
            owner_repository_id=owner_repository_id,
            root=space.root,
            path=candidate,
        )

    def prove_scope(self) -> None:
        RepositoryPathSpace.from_root(self.root).relative(
            self.path, label="repository-scoped content path"
        )

    def prove_owned(self) -> None:
        self.prove_scope()
        RepositoryPathSpace.from_root(self.root).require_owned_tree()


@dataclass(frozen=True, slots=True)
class HostSandboxPathMapping:
    """One exact host subtree translated into a stable sandbox namespace."""

    host_root: Path
    sandbox_root: PurePosixPath

    @classmethod
    def create(cls, host_root: Path, sandbox_root: PurePosixPath) -> HostSandboxPathMapping:
        return cls(host_root=canonical_path(host_root), sandbox_root=sandbox_root)

    def map_text(self, value: str) -> str | None:
        text = str(self.host_root)
        if value == text:
            return str(self.sandbox_root)
        prefix = text + os.sep
        if value.startswith(prefix):
            suffix = value[len(prefix) :]
            return str(self.sandbox_root / PurePosixPath(suffix))
        return None


def map_host_text_to_sandbox(value: str, mappings: tuple[HostSandboxPathMapping, ...]) -> str:
    """Translate only exact admitted host roots; never infer path aliases."""

    for mapping in mappings:
        mapped = mapping.map_text(value)
        if mapped is not None:
            return mapped
    return value


@dataclass(frozen=True, slots=True)
class StrictHostPathAuthority:
    """Deployment-owned execution-environment search roots for strict preparation."""

    trusted_roots: tuple[Path, ...]
    tool_search_paths: tuple[Path, ...]
    runtime_inspector: Path

    @classmethod
    def create(
        cls,
        *,
        trusted_roots: tuple[Path, ...],
        tool_search_paths: tuple[Path, ...],
        runtime_inspector: Path,
    ) -> StrictHostPathAuthority:
        roots = _trusted_host_roots(trusted_roots)
        if not tool_search_paths:
            raise FleetPlanInvalid("Strict deployment path authority requires tool_search_paths")
        search_paths: list[Path] = []
        for value in tool_search_paths:
            directory = require_absolute_host_path(value, label="tool search path")
            if not directory.is_dir():
                raise FleetPlanInvalid(
                    f"Strict execution-environment tool search path is not a directory: {directory}"
                )
            _require_under_trusted_root(
                directory, roots, label="strict execution-environment tool search path"
            )
            if directory not in search_paths:
                search_paths.append(directory)
        inspector = require_absolute_host_path(
            runtime_inspector, label="runtime dependency inspector"
        )
        if not inspector.is_file():
            raise FleetPlanInvalid(
                f"Runtime dependency inspector is not a regular file: {inspector}"
            )
        _require_under_trusted_root(inspector, roots, label="runtime dependency inspector")
        return cls(
            trusted_roots=roots,
            tool_search_paths=tuple(search_paths),
            runtime_inspector=inspector,
        )

    def tool(self, name: str) -> Path:
        """Resolve one required tool only from the execution environment's admitted search path."""

        if not name or "/" in name or "\\" in name or "=" in name or "\x00" in name:
            raise FleetPlanInvalid(f"Invalid execution-environment tool name: {name!r}")
        for directory in self.tool_search_paths:
            entry = directory / name
            # The deployment admits the name-to-entry mapping by placing the entry in an
            # operator-owned search directory.  Some tool distributions (notably NVM's
            # npm/npx) intentionally expose symlinks from bin/ into a sibling package
            # tree.  Validate authority at the admitted directory entry, then freeze the
            # dereferenced executable bytes/support tree during run preparation.
            if not entry.exists() or not os.access(entry, os.X_OK):
                continue
            _require_under_trusted_root(
                entry, self.trusted_roots, label=f"strict execution-environment tool entry {name}"
            )
            try:
                candidate = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                continue
            if not entry.is_symlink():
                _require_under_trusted_root(
                    candidate,
                    self.trusted_roots,
                    label=f"strict execution-environment tool {name}",
                )
            return candidate
        searched = ", ".join(str(path) for path in self.tool_search_paths)
        raise FleetPlanInvalid(
            f"Strict execution environment is missing required tool {name!r}; "
            f"searched admitted tool_search_paths: {searched}"
        )


def _trusted_host_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    if not values:
        raise FleetPlanInvalid("Strict deployment path authority requires trusted_host_roots")
    roots: list[Path] = []
    for value in values:
        root = require_absolute_host_path(value, label="trusted host root")
        if root == Path(root.anchor):
            raise FleetPlanInvalid("Strict trusted host root must not be the filesystem root")
        if not root.is_dir():
            raise FleetPlanInvalid(f"Strict trusted host root is not a directory: {root}")
        if root not in roots:
            roots.append(root)
    return tuple(sorted(roots, key=str))


def _require_under_trusted_root(path: Path, roots: tuple[Path, ...], *, label: str) -> None:
    if not any(path == root or path.is_relative_to(root) for root in roots):
        configured = ", ".join(str(root) for root in roots)
        raise FleetPlanInvalid(
            f"{label} is outside deployment trusted_host_roots: {path}; roots={configured}"
        )


def ensure_private_directory(path: Path) -> Path:
    """Create one Oh Goon-owned directory and keep its host permissions owner-only."""

    target = canonical_path(path)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        target.chmod(0o700)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Cannot enforce private Oh Goon directory permissions: {target}"
        ) from exc
    return target


def canonical_path(path: Path | str) -> Path:
    """Canonicalize one host path without user-home expansion."""

    return Path(path).resolve()


def navigation_path(path: Path | str) -> Path:
    """Canonicalize a user-selected source/navigation path, including ``~``.

    This helper is intentionally not an execution-authority primitive.  It is for
    read/write authoring locations selected by the user before plan admission.
    Execution/host authority must use the stricter path-space types instead.
    """

    return Path(path).expanduser().resolve()


def contained_relative_parts(value: str, *, label: str) -> PurePosixPath:
    """Parse canonical portable relative syntax while allowing contained ``..`` navigation."""

    if not value or "\x00" in value:
        raise FleetPlanInvalid(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise FleetPlanInvalid(f"{label} must use '/' separators: {value}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or PureWindowsPath(value).drive:
        raise FleetPlanInvalid(f"{label} must be relative: {value}")
    if relative.as_posix() != value:
        raise FleetPlanInvalid(f"{label} must use canonical relative syntax: {value}")
    return relative


def repository_relative_parts(value: str, *, label: str) -> PurePosixPath:
    """Parse portable repository-relative syntax without traversal aliases."""

    if not value or "\x00" in value:
        raise FleetPlanInvalid(f"{label} must be a non-empty repository-relative path")
    if "\\" in value:
        raise FleetPlanInvalid(f"{label} must use '/' separators: {value}")
    relative = PurePosixPath(value)
    if relative.is_absolute() or PureWindowsPath(value).drive:
        raise FleetPlanInvalid(f"{label} must be repository-relative: {value}")
    if ".." in relative.parts:
        raise FleetPlanInvalid(f"{label} must not contain '..' traversal: {value}")
    if relative.as_posix() != value:
        raise FleetPlanInvalid(f"{label} must use canonical repository-relative syntax: {value}")
    return relative


def require_absolute_host_path(value: str | Path, *, label: str) -> Path:
    """Resolve operator-owned host authority from one literal absolute path."""

    text = str(value)
    if not text or "\x00" in text:
        raise FleetPlanInvalid(f"{label} must be a non-empty absolute path")
    candidate = Path(text)
    if not candidate.is_absolute():
        raise FleetPlanInvalid(f"{label} must be an absolute path: {text}")
    if "~" in candidate.parts or ".." in candidate.parts:
        raise FleetPlanInvalid(f"{label} must not contain home/traversal aliases: {text}")
    return candidate.resolve()


def storage_component(value: str) -> str:
    """Collision-resistant host storage key for untrusted logical identifiers."""

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    readable = "".join(
        character if character.isalnum() or character in "-_" else "_" for character in value
    ).strip("_")[:40]
    prefix = readable or "item"
    return f"{prefix}-{digest}"
