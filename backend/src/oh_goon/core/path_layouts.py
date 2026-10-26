from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import (
    canonical_path,
    ensure_private_directory,
    storage_component,
)


@dataclass(frozen=True, slots=True)
class ApplicationStatePathLayout:
    """Canonical persistent filesystem layout owned by one oh-goon database root."""

    root: Path

    @classmethod
    def from_database(cls, database_path: Path) -> ApplicationStatePathLayout:
        database = canonical_path(database_path)
        return cls(root=database.parent)

    @property
    def extensions(self) -> Path:
        return self.root / "extensions"

    @property
    def plan_tools(self) -> Path:
        return self.root / "plan-tools"

    @property
    def content_store(self) -> Path:
        return self.root / "content-store"

    @property
    def run_workspaces(self) -> Path:
        return self.root / "run-workspaces"

    @property
    def managed_workspaces(self) -> Path:
        """Durable candidate/source workspace containers; distinct from Run scratch."""

        return self.root / "workspaces"

    @property
    def executor_control(self) -> Path:
        """Private host-control state for local executor locks and recovery metadata."""

        return self.root / "executor-control"

    @property
    def local_launches(self) -> Path:
        """Private per-launch structured facts; not a process or authority registry."""

        return self.root / "local-launches"

    @property
    def local_launch_bindings(self) -> Path:
        """Exact workspace-to-launch bindings; never discovered by directory recency."""

        return self.root / "local-launch-bindings"

    @property
    def local_launch_access(self) -> Path:
        """Owner-private launch capabilities; never lifecycle or discovery authority."""

        return self.root / "local-launch-access"

    @property
    def diagnostics_log(self) -> Path:
        """Passive local diagnostic evidence; never an operational state authority."""

        return self.root / "diagnostics" / "diagnostics.jsonl"

    @property
    def private_control_paths(self) -> tuple[Path, ...]:
        """Private cross-Run state, excluding explicitly disclosed execution scratch."""
        return (
            self.content_store,
            self.executor_control,
            self.local_launches,
            self.local_launch_bindings,
            self.local_launch_access,
            self.diagnostics_log,
        )


@dataclass(frozen=True, slots=True)
class ManagedPreparationPathLayout:
    """Private filesystem layout for Fleet-owned acquisition/preparation subprocesses."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> ManagedPreparationPathLayout:
        return cls(root=canonical_path(root) / ".preparation")

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def config(self) -> Path:
        return self.root / "config"

    def ensure(self) -> None:
        for path in (self.root, self.home, self.tmp, self.cache, self.config):
            ensure_private_directory(path)


@dataclass(frozen=True, slots=True)
class RunPathLayout:
    """The only owner of run-private host filesystem layout."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> RunPathLayout:
        return cls(root=canonical_path(root))

    @property
    def base_workspace(self) -> Path:
        return self.root / "sources" / "repository"

    @property
    def runtime_root(self) -> Path:
        return self.root / "runtime-root"

    @property
    def toolchain(self) -> Path:
        return self.root / "toolchain"

    @property
    def managed_inputs(self) -> Path:
        return self.root / "managed-inputs"

    @property
    def content_inputs(self) -> Path:
        return self.root / "content-inputs"

    @property
    def published_workspaces(self) -> Path:
        return self.root / "published"

    @property
    def published_caches(self) -> Path:
        return self.root / "published-cache"

    def attempt_root(self, step_id: str, attempt_number: int) -> Path:
        return self.root / "attempts" / storage_component(step_id) / f"{attempt_number:02d}"

    def step_attempts_root(self, step_id: str) -> Path:
        return self.root / "attempts" / storage_component(step_id)

    def published_workspace(self, step_id: str) -> Path:
        return self.published_workspaces / storage_component(step_id)

    def published_cache(self, step_id: str) -> Path:
        return self.published_caches / storage_component(step_id)

    def content_input(self, index: int) -> Path:
        return self.content_inputs / f"{index:03d}"

    def managed_input(self, index: int) -> Path:
        return self.managed_inputs / f"{index:03d}"

    def package_source(self, digest: str) -> Path:
        return self.root / "sources" / "goon-projects" / storage_component(digest)


@dataclass(frozen=True, slots=True)
class AttemptPathLayout:
    """The only owner of one attempt's private host filesystem layout."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> AttemptPathLayout:
        return cls(root=canonical_path(root))

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def uv_build_project(self) -> Path:
        return self.cache / "uv-build-project"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def etc(self) -> Path:
        return self.root / "etc"


@dataclass(frozen=True, slots=True)
class ContentStorePathLayout:
    """The only owner of durable retained-content store layout."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> ContentStorePathLayout:
        return cls(root=canonical_path(root))

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def references(self) -> Path:
        return self.root / "references"

    @property
    def leases(self) -> Path:
        return self.root / "leases"

    @property
    def object_lifecycle(self) -> Path:
        return self.root / "object-lifecycle"

    @property
    def lock(self) -> Path:
        return self.root / "store.lock"

    def objects(self, kind: str) -> Path:
        if kind not in {"tree", "file"}:
            raise FleetPlanInvalid(f"Unsupported Fleet content-store kind: {kind}")
        return self.root / "objects" / kind

    def object_path(self, kind: str, digest: str) -> Path:
        return self.objects(kind) / digest

    def repository_references(self, owner_repository_id: str) -> Path:
        return self.references / storage_component(owner_repository_id)

    def repository_leases(self, owner_repository_id: str) -> Path:
        return self.leases / storage_component(owner_repository_id)

    def reference(self, owner_repository_id: str, reference_id: str) -> Path:
        return self.repository_references(owner_repository_id) / f"{reference_id}.json"

    def lease(self, owner_repository_id: str, lease_id: str) -> Path:
        return self.repository_leases(owner_repository_id) / f"{lease_id}.json"

    def stage(self, kind: str, nonce: str) -> Path:
        return self.staging / f"{kind}-{nonce}"


@dataclass(frozen=True, slots=True)
class SandboxPathLayout:
    """Canonical in-sandbox mount namespace shared by every isolation backend."""

    workspace: PurePosixPath = PurePosixPath("/workspace")
    home: PurePosixPath = PurePosixPath("/home/fleet")
    tmp: PurePosixPath = PurePosixPath("/tmp")
    cache: PurePosixPath = PurePosixPath("/cache")
    config: PurePosixPath = PurePosixPath("/config")
    etc: PurePosixPath = PurePosixPath("/etc")
    proc: PurePosixPath = PurePosixPath("/proc")
    dev: PurePosixPath = PurePosixPath("/dev")
    toolchain: PurePosixPath = PurePosixPath("/toolchain/bin")
    managed: PurePosixPath = PurePosixPath("/managed")

    def managed_path(self, index: int) -> PurePosixPath:
        return self.managed / f"{index:03d}"

    @property
    def reserved_roots(self) -> tuple[PurePosixPath, ...]:
        return (
            self.workspace,
            PurePosixPath("/home"),
            self.tmp,
            self.cache,
            self.config,
            self.etc,
            self.proc,
            self.dev,
            PurePosixPath("/toolchain"),
            self.managed,
        )


SANDBOX_PATHS = SandboxPathLayout()
