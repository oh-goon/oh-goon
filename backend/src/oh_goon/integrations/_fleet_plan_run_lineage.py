from __future__ import annotations

import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedPlanStep
from oh_goon.core.models.fleet_plan_evidence import PlanContentObjectEvidence
from oh_goon.core.models.fleet_plan_step_contract import (
    RetainedPlanCacheSource,
    StepPlanCacheSource,
    StepWorkspaceSource,
)
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.path_layouts import SANDBOX_PATHS, RunPathLayout
from oh_goon.integrations._fleet_plan_run_resources import _FleetPlanRunInputs
from oh_goon.integrations.content_snapshot import (
    SnapshotBinding,
    SnapshotIdentity,
    copy_frozen_directory,
    snapshot_directory,
    snapshot_identity,
)
from oh_goon.integrations.fleet_content_store import FleetContentObject, FleetContentStore


@dataclass(frozen=True, slots=True)
class _ContentCacheInput:
    path: Path
    identity: SnapshotIdentity
    evidence: PlanContentObjectEvidence
    direct_read_only: bool = False


class _RunLineage:
    """Own run-local snapshot publication, consumption, retention, and release."""

    def __init__(
        self,
        root: Path,
        inputs: _FleetPlanRunInputs,
        steps: tuple[ResolvedPlanStep, ...],
        cancel_requested: Callable[[], bool] | None,
        content_store: FleetContentStore | None,
        owner_repository_id: str,
    ) -> None:
        self._layout = RunPathLayout.from_root(root)
        self._inputs = inputs
        # The constructor callback belongs only to immutable-input capture. It may
        # close immediately after preparation, so execution must explicitly rebind.
        self._cancel_requested: Callable[[], bool] | None = cancel_requested
        self._content_store = content_store
        self._owner_repository_id = owner_repository_id
        self._content_cache_inputs = _capture_content_cache_inputs(
            self._layout,
            steps,
            content_store,
            owner_repository_id,
            cancel_requested,
        )
        self._content_cache_evidence = tuple(
            binding.evidence for _, binding in sorted(self._content_cache_inputs.items())
        )
        self._published: dict[str, tuple[Path, SnapshotIdentity]] = {}
        self._published_caches: dict[str, tuple[Path, SnapshotIdentity]] = {}
        self._workspace_consumers = Counter(
            source.step_id
            for step in steps
            for source in (step.workspace.source,)
            if isinstance(source, StepWorkspaceSource)
        )
        self._base_consumers = sum(
            not isinstance(step.workspace.source, StepWorkspaceSource) for step in steps
        )
        self._cache_consumers = Counter(
            source.step_id
            for step in steps
            for source in (step.cache.source,)
            if isinstance(source, StepPlanCacheSource)
        )
        self._content_cache_consumers = Counter(
            _content_cache_key(source)
            for step in steps
            for source in (step.cache.source,)
            if isinstance(source, RetainedPlanCacheSource)
        )
        self._managed_consumers = Counter(
            str(binding.snapshot_path)
            for step in steps
            for binding in inputs.step_binding(step).sandbox_bindings
        )

    def clear_preparation_cancellation_observation(self) -> None:
        """Drop preparation-only cancellation authority before it can escape."""
        self._cancel_requested = None

    def bind_execution_cancel_requested(self, cancel_requested: Callable[[], bool] | None) -> None:
        """Bind the callback valid for the workload/execution lifetime."""
        self._cancel_requested = cancel_requested

    @property
    def content_store(self) -> FleetContentStore | None:
        return self._content_store

    def content_input_evidence(self) -> tuple[PlanContentObjectEvidence, ...]:
        return self._content_cache_evidence

    def source(self, step: ResolvedPlanStep) -> tuple[Path, SnapshotIdentity]:
        source = step.workspace.source
        if isinstance(source, StepWorkspaceSource):
            try:
                return self._published[source.step_id]
            except KeyError as exc:
                raise FleetPlanInvalid(
                    f"Fleet plan step {step.step_id} requires unavailable workspace {source.step_id}"
                ) from exc
        return self._inputs.base_workspace, self._inputs.base_identity

    def seed_cache(self, step: ResolvedPlanStep, destination: Path) -> SnapshotIdentity:
        source = step.cache.source
        if isinstance(source, RetainedPlanCacheSource):
            key = _content_cache_key(source)
            try:
                binding = self._content_cache_inputs[key]
            except KeyError as exc:
                raise FleetPlanInvalid(
                    f"Fleet plan step {step.step_id} requires unavailable captured cache input {key}"
                ) from exc
            if source.access == "read_only":
                if not binding.direct_read_only:
                    raise FleetPlanInvalid(
                        f"Fleet plan step {step.step_id} read-only retained cache was not captured as immutable direct input"
                    )
                destination.mkdir()
                return binding.identity
            observed = copy_frozen_directory(
                binding.path,
                destination,
                label=f"Fleet step {step.step_id} retained cache input",
                cancel_requested=self._cancel_requested,
            )
            if observed != binding.identity:
                raise FleetPlanInvalid(
                    f"Fleet plan step {step.step_id} retained cache input changed after run preparation"
                )
            return binding.identity
        if not isinstance(source, StepPlanCacheSource):
            destination.mkdir()
            return snapshot_identity(destination, cancel_requested=self._cancel_requested)
        try:
            published, expected = self._published_caches[source.step_id]
        except KeyError as exc:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} requires unavailable cache {source.step_id}"
            ) from exc
        observed = copy_frozen_directory(
            published,
            destination,
            label=f"Fleet step {step.step_id} cache source",
            cancel_requested=self._cancel_requested,
        )
        if observed != expected:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} cache source changed while creating attempt"
            )
        return expected

    def read_only_cache_binding(self, step: ResolvedPlanStep) -> SnapshotBinding | None:
        source = step.cache.source
        if not isinstance(source, RetainedPlanCacheSource):
            return None
        if isinstance(source, RetainedPlanCacheSource) and source.access != "read_only":
            return None
        key = _content_cache_key(source)
        try:
            binding = self._content_cache_inputs[key]
        except KeyError as exc:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} requires unavailable captured cache input {key}"
            ) from exc
        if not binding.direct_read_only:
            raise FleetPlanInvalid(
                f"Fleet plan step {step.step_id} read-only retained cache has no direct immutable binding"
            )
        return SnapshotBinding(
            declared_path=binding.path,
            snapshot_path=binding.path,
            identity=binding.identity,
            sandbox_path=SANDBOX_PATHS.cache,
        )

    def publish_workspace(self, step: ResolvedPlanStep, workspace: Path) -> None:
        if not step.workspace.publish:
            return
        destination = self._layout.published_workspace(step.step_id)
        identity = snapshot_directory(
            workspace,
            destination,
            label=f"Fleet step {step.step_id} published workspace",
            cancel_requested=self._cancel_requested,
        )
        self._published[step.step_id] = (destination, identity)

    def publish_cache(
        self, step: ResolvedPlanStep, cache: Path
    ) -> tuple[SnapshotIdentity | None, FleetContentObject | None]:
        if not step.cache.publish:
            return None, None
        destination = self._layout.published_cache(step.step_id)
        identity = snapshot_directory(
            cache,
            destination,
            label=f"Fleet step {step.step_id} published cache",
            cancel_requested=self._cancel_requested,
        )
        self._published_caches[step.step_id] = (destination, identity)
        retained = None
        if step.cache.retain:
            if self._content_store is None:
                raise FleetPlanInvalid(
                    f"Fleet plan step {step.step_id} requested retained cache without a content store"
                )
            retained = self._content_store.put_tree(
                RepositoryScopedPath.create(
                    owner_repository_id=self._owner_repository_id,
                    root=destination,
                    path=destination,
                    label=f"Fleet step {step.step_id} retained cache",
                ),
                cancel_requested=self._cancel_requested,
            )
        return identity, retained

    def discard_publications(self, step: ResolvedPlanStep) -> None:
        """Roll back run-local publications created by one unsuccessful step."""

        _drop_snapshot(self._published, step.step_id)
        _drop_snapshot(self._published_caches, step.step_id)

    def release(self, step: ResolvedPlanStep) -> None:
        self._release_workspace(step)
        self._release_cache(step)
        self._release_managed_inputs(step)
        if step.cache.publish and self._cache_consumers[step.step_id] <= 0:
            _drop_snapshot(self._published_caches, step.step_id)
        if step.workspace.publish and self._workspace_consumers[step.step_id] <= 0:
            _drop_snapshot(self._published, step.step_id)

    def _release_workspace(self, step: ResolvedPlanStep) -> None:
        source = step.workspace.source
        if isinstance(source, StepWorkspaceSource):
            self._workspace_consumers[source.step_id] -= 1
            if self._workspace_consumers[source.step_id] <= 0:
                _drop_snapshot(self._published, source.step_id)
        elif self._base_consumers > 0:
            self._base_consumers -= 1
            if self._base_consumers == 0:
                _remove_snapshot(self._inputs.base_workspace)

    def _release_cache(self, step: ResolvedPlanStep) -> None:
        source = step.cache.source
        if isinstance(source, StepPlanCacheSource):
            self._cache_consumers[source.step_id] -= 1
            if self._cache_consumers[source.step_id] <= 0:
                _drop_snapshot(self._published_caches, source.step_id)
        elif isinstance(source, RetainedPlanCacheSource):
            key = _content_cache_key(source)
            self._content_cache_consumers[key] -= 1
            if self._content_cache_consumers[key] <= 0:
                binding = self._content_cache_inputs.pop(key, None)
                if binding is not None and not binding.direct_read_only:
                    _remove_snapshot(binding.path)

    def _release_managed_inputs(self, step: ResolvedPlanStep) -> None:
        for binding in self._inputs.step_binding(step).sandbox_bindings:
            key = str(binding.snapshot_path)
            self._managed_consumers[key] -= 1
            if self._managed_consumers[key] <= 0:
                _remove_snapshot(binding.snapshot_path)


def _content_cache_key(source: RetainedPlanCacheSource) -> str:
    return source.reference_id


def _capture_content_cache_inputs(
    layout: RunPathLayout,
    steps: tuple[ResolvedPlanStep, ...],
    content_store: FleetContentStore | None,
    owner_repository_id: str,
    cancel_requested: Callable[[], bool] | None,
) -> dict[str, _ContentCacheInput]:
    retained_sources = [
        source
        for step in steps
        for source in (step.cache.source,)
        if isinstance(source, RetainedPlanCacheSource)
    ]
    reference_ids = sorted({source.reference_id for source in retained_sources})
    if reference_ids and content_store is None:
        raise FleetPlanInvalid("Fleet plan requires retained cache objects without a content store")
    modes: dict[str, set[str]] = {}
    for source in retained_sources:
        modes.setdefault(source.reference_id, set()).add(source.access)
    bindings: dict[str, _ContentCacheInput] = {}
    for index, reference_id in enumerate(reference_ids):
        assert content_store is not None
        direct_read_only = modes.get(reference_id) == {"read_only"}
        if direct_read_only:
            retained = content_store.get(
                reference_id,
                owner_repository_id=owner_repository_id,
                expected_kind="tree",
                cancel_requested=cancel_requested,
            )
            identity = retained.identity
            path = retained.path
        else:
            destination = layout.content_input(index)
            identity = content_store.materialize_tree(
                reference_id,
                RepositoryScopedPath.create(
                    owner_repository_id=owner_repository_id,
                    root=layout.content_inputs,
                    path=destination,
                    label=f"Fleet retained cache input {reference_id}",
                ),
                cancel_requested=cancel_requested,
            )
            path = destination
        bindings[reference_id] = _ContentCacheInput(
            path=path,
            identity=identity,
            evidence=PlanContentObjectEvidence(
                kind="tree",
                sha256=identity.sha256,
                byte_count=identity.byte_count,
                entry_count=identity.entry_count,
            ),
            direct_read_only=direct_read_only,
        )

    return bindings


def _drop_snapshot(store: dict[str, tuple[Path, SnapshotIdentity]], step_id: str) -> None:
    binding = store.pop(step_id, None)
    if binding is not None:
        _remove_snapshot(binding[0])


def _remove_snapshot(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
