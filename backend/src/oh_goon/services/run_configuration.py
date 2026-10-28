"""Capture one run.yml and select existing source/target owners without execution."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError
from yaml.tokens import AliasToken, AnchorToken, TagToken

from oh_goon.core.errors import (
    FleetPlanInvalid,
    FleetPlanSourceAcquisitionRequired,
    RunConfigurationInvalid,
)
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.git_sources import LockedExecutionSource
from oh_goon.core.models.repository import RepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.models.run_configuration import (
    CatalogPlanSelection,
    FilePlanSelection,
    GitPlanSelection,
    RunConfiguration,
)
from oh_goon.core.path_authority import ContainedPathSpace
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.runtime.template_discovery import template_package
from oh_goon.services.fleet_caller_file_freshness import (
    locate_caller_file,
    read_caller_file_bytes,
    require_caller_file_fresh,
    stat_caller_file,
)

if TYPE_CHECKING:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


class _RunYamlLoader(yaml.SafeLoader):
    """Strict mappings, with no implicit merge or duplicate member authority."""


def _unique_mapping(
    loader: _RunYamlLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, object]:
    values: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str or key == "<<":
            raise RunConfigurationInvalid(
                "run.yml requires ordinary string keys; merges are prohibited",
                diagnostic_code="CONFIG_SCHEMA_INVALID",
            )
        if key in values:
            raise RunConfigurationInvalid(
                "run.yml contains a duplicate mapping key",
                diagnostic_code="CONFIG_SCHEMA_INVALID",
            )
        values[key] = loader.construct_object(value_node, deep=deep)
    return values


_RunYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _regular_file(path: Path, root: Path) -> tuple[Path, str]:
    try:
        return locate_caller_file(path, root=root)
    except FleetPlanInvalid as exc:
        raise RunConfigurationInvalid(
            str(exc),
            diagnostic_code="CONFIG_PATH_INVALID",
        ) from exc


def _no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise RunConfigurationInvalid(
                    "run.yml paths cannot contain symlink components",
                    diagnostic_code="CONFIG_PATH_INVALID",
                )
        except OSError as exc:
            raise RunConfigurationInvalid(
                "run.yml selected path is unavailable", diagnostic_code="CONFIG_PATH_INVALID"
            ) from exc


@dataclass(frozen=True, slots=True)
class RunConfigurationSnapshot:
    selection: RunConfiguration
    content: bytes
    identity: CallerInputFileIdentity

    @property
    def relative_path(self) -> str:
        return self.identity.relative_path

    @property
    def sha256(self) -> str:
        return self.identity.sha256

    @property
    def root(self) -> Path:
        return self.identity.caller_root

    @property
    def path(self) -> Path:
        return self.root / self.identity.relative_path

    @property
    def lock_path(self) -> Path:
        return self.root / "goon.lock"

    @property
    def inputs(self) -> dict[str, PlanInputValue]:
        return self.selection.caller_inputs()

    def authority(self) -> CallerInputFileIdentity:
        return self.identity

    def external_artifacts(self) -> tuple[tuple[str, bytes], ...]:
        return ((self.identity.relative_path, self.content),)

    def require_fresh(self) -> None:
        try:
            require_caller_file_fresh(self.identity)
        except FleetPlanInvalid as exc:
            raise RunConfigurationInvalid(str(exc)) from exc

    def source_path(
        self,
        repository_path: Path | None = None,
        *,
        plan_templates: PlanTemplateStore | None = None,
        acquired_plan: Path | None = None,
    ) -> Path:
        source = self.selection.source
        if isinstance(source, GitPlanSelection):
            if acquired_plan is None:
                raise FleetPlanSourceAcquisitionRequired(
                    "Git Plan source requires explicit acquisition with lock; "
                    "inspect its contract first with 'oh-goon config inspect run.yml --acquire'"
                )
            return acquired_plan
        if isinstance(source, CatalogPlanSelection):
            store = plan_templates or PlanTemplateStore()
            _, directory = template_package(
                store.root, source.name, source_name="plan.yml", kind="Plan"
            )
            return directory / "plan.yml"
        if isinstance(source, FilePlanSelection):
            if Path(source.path).name == "goon.yml":
                raise RunConfigurationInvalid(
                    "file-plan selects a Plan; select the target root Goon with repository-goon",
                    diagnostic_code="CONFIG_SCHEMA_INVALID",
                    fields=("source.path",),
                )
            if Path(source.path).is_absolute():
                raise RunConfigurationInvalid(
                    "file-plan.path must be relative to the workflow root",
                    diagnostic_code="CONFIG_PATH_INVALID",
                )
            return _regular_file(Path(source.path), self.root)[0]
        if repository_path is None:
            raise RunConfigurationInvalid(
                "repository-goon requires the explicitly registered repository",
                diagnostic_code="CONFIG_PATH_INVALID",
            )
        return _regular_file(Path("goon.yml"), repository_path)[0]

    def agent_repository_root(self) -> Path | None:
        if self.selection.agent is None:
            return None
        root = Path(self.selection.agent.repository_root)
        if not root.is_absolute() or ".." in root.parts:
            raise RunConfigurationInvalid(
                "agent.repository_root must be an explicit absolute directory",
                diagnostic_code="CONFIG_PATH_INVALID",
            )
        _no_symlink_components(root)
        if not root.is_dir():
            raise RunConfigurationInvalid(
                "agent.repository_root must be an existing directory",
                diagnostic_code="CONFIG_PATH_INVALID",
            )
        return root

    def agent_skill_root(self) -> Path | None:
        if self.selection.agent is None:
            return None
        selected = Path(self.selection.agent.skill_root)
        if selected.is_absolute() or ".." in selected.parts:
            raise RunConfigurationInvalid(
                "agent.skill_root must be contained in the workflow root",
                diagnostic_code="CONFIG_PATH_INVALID",
            )
        root = self.root / selected
        _no_symlink_components(root)
        if not root.is_dir():
            raise RunConfigurationInvalid(
                "agent.skill_root must be an existing directory",
                diagnostic_code="CONFIG_PATH_INVALID",
            )
        return root


@dataclass(frozen=True, slots=True)
class NormalizedRunConfiguration:
    snapshot: RunConfigurationSnapshot
    repository: RepositoryIdentity
    plan_path: Path

    @property
    def inputs(self) -> dict[str, PlanInputValue]:
        return self.snapshot.inputs


def load_run_configuration(path: Path) -> RunConfigurationSnapshot:
    """Read one bounded stable byte cut; downstream operations transport its authority."""
    selected_path = path.absolute()
    if ".." in selected_path.parts:
        raise RunConfigurationInvalid(
            "run.yml path cannot contain parent traversal", diagnostic_code="CONFIG_PATH_INVALID"
        )
    _no_symlink_components(selected_path)
    root = selected_path.parent
    selected, relative = _regular_file(selected_path, root)
    before = stat_caller_file(selected)
    try:
        data = read_caller_file_bytes(selected, expected=before)
    except FleetPlanInvalid as exc:
        raise RunConfigurationInvalid(str(exc), diagnostic_code="CONFIG_PATH_INVALID") from exc
    try:
        content = data.decode("utf-8", errors="strict")
        if any(
            isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(content)
        ):
            raise RunConfigurationInvalid(
                "run.yml aliases, anchors and custom tags are prohibited",
                diagnostic_code="CONFIG_SCHEMA_INVALID",
            )
        payload = yaml.load(content, Loader=_RunYamlLoader)
        selection = RunConfiguration.model_validate(payload)
    except ValidationError as exc:
        fields = tuple(
            ".".join(
                str(part)
                for part in error["loc"]
                if part not in {"catalog-plan", "file-plan", "repository-goon", "git-plan"}
            )
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise RunConfigurationInvalid(
            "run.yml is invalid; correct the named version 1 selection fields",
            diagnostic_code="CONFIG_SCHEMA_INVALID",
            fields=fields,
        ) from exc
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise RunConfigurationInvalid(
            "run.yml must contain valid UTF-8 safe YAML", diagnostic_code="CONFIG_SCHEMA_INVALID"
        ) from exc
    after = stat_caller_file(selected)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RunConfigurationInvalid("run.yml changed during source capture")
    identity = CallerInputFileIdentity(
        caller_root=root,
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        source_device=before.st_dev,
        source_inode=before.st_ino,
    )
    return RunConfigurationSnapshot(selection, data, identity)


def _configuration_repository(
    snapshot: RunConfigurationSnapshot,
    repositories: Iterable[RepositoryIdentity | RepositoryCatalogEntry],
) -> RepositoryIdentity:
    matches = []
    for entry in repositories:
        if isinstance(entry, RepositoryIdentity):
            repository = entry
        else:
            if entry.scope is not FleetScope.INCLUDED:
                continue
            repository = entry.repository
        if repository.repository_id == snapshot.selection.target.repository_id:
            matches.append(repository)
    if len(matches) != 1:
        raise RunConfigurationInvalid(
            "run.yml target must name exactly one included registered repository",
            diagnostic_code="CONFIG_TARGET_UNAVAILABLE",
            fields=("target.repository_id",),
        )
    repository = matches[0]
    snapshot.require_fresh()
    snapshot.agent_repository_root()
    snapshot.agent_skill_root()
    if snapshot.selection.repair is not None:
        from oh_goon.integrations.agent_patch_apply import validate_write_roots

        try:
            validate_write_roots(snapshot.selection.repair.write_roots)
        except FleetPlanInvalid as exc:
            raise RunConfigurationInvalid(
                "repair requires explicit contained write roots",
                diagnostic_code="CONFIG_SCHEMA_INVALID",
                fields=("repair.write_roots",),
            ) from exc
    return repository


def normalize_run_configuration(
    snapshot: RunConfigurationSnapshot,
    repositories: Iterable[RepositoryIdentity | RepositoryCatalogEntry],
    *,
    plan_templates: PlanTemplateStore | None = None,
    acquired_plan: Path | None = None,
) -> NormalizedRunConfiguration:
    """Select an explicit registered target; discovery and execution remain original owners."""
    repository = _configuration_repository(snapshot, repositories)
    source = snapshot.source_path(
        repository.path, plan_templates=plan_templates, acquired_plan=acquired_plan
    )
    return NormalizedRunConfiguration(snapshot, repository, source)


def capture_run_configuration_source(
    snapshot: RunConfigurationSnapshot,
    repositories: Iterable[RepositoryIdentity | RepositoryCatalogEntry],
    resolver: FleetPlanResolver,
    *,
    acquire: bool = False,
    allow_network: bool = False,
    update: bool = False,
    update_applications: bool = False,
) -> tuple[NormalizedRunConfiguration, FleetPlanSourceGraph]:
    """Acquire only explicitly, then capture one original source graph before closing Git."""
    from oh_goon.core.errors import ExecutionLockRejected
    from oh_goon.core.execution_lock_identity import verify_execution_lock
    from oh_goon.integrations.git_source_preparation import prepare_git_source
    from oh_goon.services.execution_lock_persistence import (
        MAX_EXECUTION_LOCK_BYTES,
        parse_execution_lock,
    )

    repository = _configuration_repository(snapshot, repositories)
    locked: tuple[LockedExecutionSource, ...] | None = ()
    if snapshot.lock_path.exists() or snapshot.lock_path.is_symlink():
        try:
            lock_path, _ = locate_caller_file(snapshot.lock_path, root=snapshot.root)
            lock_bytes = read_caller_file_bytes(
                lock_path, expected=stat_caller_file(lock_path), max_bytes=MAX_EXECUTION_LOCK_BYTES
            )
            lock = parse_execution_lock(lock_bytes.decode("utf-8"))
            verify_execution_lock(lock, lock)
            locked = lock.execution_sources
        except (FleetPlanInvalid, OSError, UnicodeError, ValueError) as exc:
            if acquire or allow_network:
                if isinstance(exc, ExecutionLockRejected):
                    raise
                raise ExecutionLockRejected(
                    "Existing goon.lock is unavailable or invalid; restore its source pins before locking",
                    reason="unreadable",
                ) from exc
            locked = None
        else:
            if not acquire and allow_network:
                expected = next(
                    (item.digest for item in lock.external if item.path == snapshot.relative_path),
                    None,
                )
                if expected != "sha256:" + snapshot.sha256:
                    raise ExecutionLockRejected(
                        "run.yml changed from goon.lock; explicitly lock the current selection before acquisition",
                        reason="configuration_changed",
                    )
    selection = snapshot.selection.source
    if isinstance(selection, GitPlanSelection):
        if (
            update
            and not update_applications
            and any(
                pin.kind == "application" and (pin.git, pin.ref) == (selection.git, selection.ref)
                for pin in locked or ()
            )
        ):
            raise FleetPlanInvalid(
                "Git Plan refresh conflicts with a retained application; explicitly refresh both selections"
            )
        if locked is None:
            raise FleetPlanInvalid(
                "Existing goon.lock is unavailable or invalid; restore its source pins before acquisition"
            )
        frozen = next(
            (
                item
                for item in locked
                if item.kind == "plan"
                and (item.git, item.ref, item.path, item.plan)
                == (selection.git, selection.ref, selection.path, selection.plan)
            ),
            None,
        )
        if frozen is None and not acquire:
            raise FleetPlanSourceAcquisitionRequired(
                "Git Plan requires explicit acquisition with lock; "
                "inspect its contract first with 'oh-goon config inspect run.yml --acquire'"
            )
        pinned = frozen or next(
            (item for item in locked if (item.git, item.ref) == (selection.git, selection.ref)),
            None,
        )
        with prepare_git_source(
            selection.git,
            selection.ref,
            commit=pinned.commit if pinned is not None and not update else None,
            allow_network=allow_network,
        ) as (checkout, commit):
            root = ContainedPathSpace.from_root(checkout).resolve_from(
                checkout,
                selection.path,
                label="Git example authoring root",
                kind="directory",
                require_exists=True,
            )
            plan = ContainedPathSpace.from_root(root).resolve_from(
                root, selection.plan, label="Git example Plan", kind="file", require_exists=True
            )
            if plan.name == "goon.yml":
                raise FleetPlanInvalid("Git Plan selection must name a Plan, not a Goon")
            provenance = LockedExecutionSource(
                **selection.model_dump(exclude={"kind"}), kind="plan", commit=commit
            )
            graph = resolver.analyze_source(
                plan,
                containment_root=root,
                plan_source=provenance,
                locked_sources=locked,
                acquire_sources=acquire,
                update_goons=update,
                update_applications=update_applications,
                allow_source_network=allow_network,
            )
            return NormalizedRunConfiguration(snapshot, repository, plan), graph
    plan = snapshot.source_path(repository.path)
    return NormalizedRunConfiguration(snapshot, repository, plan), resolver.analyze_source(
        plan,
        locked_sources=locked,
        acquire_sources=acquire,
        update_goons=update,
        update_applications=update_applications,
        allow_source_network=allow_network,
    )
