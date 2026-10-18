from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import MutableMapping

from oh_goon import __version__
from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchIdentity,
    LaunchIntent,
    LaunchLifecycleState,
    LaunchManifest,
)
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.core.process_birth import observe_process_birth

BACKEND_SERVICE = "backend"
FRONTEND_SERVICE = "frontend"
BACKEND_GENERATION = 1
FRONTEND_GENERATION = 1


@dataclass(frozen=True, slots=True)
class LocalLaunchOwner:
    """Small owner-side writer for the existing local launch event store.

    This type records owner facts only. It does not spawn processes, resolve packages,
    build assets, manage sockets, or replace uvicorn/npm/Vite lifecycle authority.
    """

    store: LocalLaunchEventStore
    identity: LaunchIdentity
    asserted_by: str

    @classmethod
    def create(
        cls,
        *,
        intent: LaunchIntent,
        workspace: Path,
        asserted_by: str,
        store: LocalLaunchEventStore | None = None,
        binding_store: LocalLaunchBindingStore | None = None,
        owner_pid: int | None = None,
    ) -> LocalLaunchOwner:
        target_store = LocalLaunchEventStore.configured() if store is None else store
        target_bindings = (
            LocalLaunchBindingStore.from_event_store(target_store)
            if binding_store is None
            else binding_store
        )
        identity = LaunchIdentity(
            launch_id=f"launch-{uuid.uuid4().hex}",
            workspace=str(workspace.resolve()),
            oh_goon_version=__version__,
            owner_generation=1,
        )
        timestamp = _now()
        target_store.create(
            identity=identity,
            intent=intent,
            asserted_by=asserted_by,
            timestamp=timestamp,
        )
        try:
            target_bindings.bind(
                identity=identity,
                intent=intent,
                event_store=target_store,
                timestamp=timestamp,
            )
        except BaseException:
            # No runtime was started yet. Keep the inert orphan record terminal so it
            # cannot later be confused with a live owner if inspected explicitly.
            target_store.append(
                identity.launch_id,
                event=LaunchEventKind.LAUNCH_ABORTED,
                asserted_by=asserted_by,
                timestamp=_now(),
            )
            raise
        owner = cls(store=target_store, identity=identity, asserted_by=asserted_by)
        owner.observe_owner(os.getpid() if owner_pid is None else owner_pid)
        return owner

    @classmethod
    def load(
        cls,
        launch_id: str,
        *,
        asserted_by: str,
        store: LocalLaunchEventStore | None = None,
    ) -> LocalLaunchOwner:
        target_store = LocalLaunchEventStore.configured() if store is None else store
        identity = target_store.manifest(launch_id).identity
        return cls(store=target_store, identity=identity, asserted_by=asserted_by)

    def install_runtime_context(
        self,
        *,
        backend_generation: int = BACKEND_GENERATION,
        environ: MutableMapping[str, str] | None = None,
    ) -> LocalLaunchRuntimeContext:
        context = LocalLaunchRuntimeContext(
            launch_id=self.identity.launch_id,
            owner_generation=self.identity.owner_generation,
            backend_generation=backend_generation,
        )
        context.install(environ)
        return context

    def observe_owner(self, pid: int) -> LaunchManifest | None:
        birth = observe_process_birth(pid)
        if birth is None:
            return None
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            payload={"pid": pid, "birth_identity": birth.to_payload()},
        )

    def spawned(self, service: str, generation: int, pid: int) -> LaunchManifest:
        birth = observe_process_birth(pid)
        payload: dict[str, object] = {"pid": pid}
        if birth is not None:
            payload["birth_identity"] = birth.to_payload()
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.SERVICE_SPAWNED,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            service=service,
            service_generation=generation,
            payload=payload,
        )

    def bound(self, service: str, generation: int, *, host: str, port: int) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.SERVICE_BOUND,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            service=service,
            service_generation=generation,
            payload={"host": host, "port": port},
        )

    def startup_complete(self, service: str, generation: int) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.SERVICE_STARTUP_COMPLETE,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            service=service,
            service_generation=generation,
        )

    def ready(self, service: str, generation: int) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.SERVICE_READY,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            service=service,
            service_generation=generation,
        )

    def launch_ready(self) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.LAUNCH_READY,
            asserted_by=self.asserted_by,
            timestamp=_now(),
        )

    def service_failed(
        self,
        service: str,
        generation: int,
        *,
        code: str,
        reason: str,
        retryable: bool,
        failure_kind: str = "process",
    ) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.SERVICE_FAILED,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            service=service,
            service_generation=generation,
            payload={
                "failure_kind": failure_kind,
                "failure_code": code,
                "reason": reason,
                "retryable": retryable,
            },
        )

    def launch_failed(
        self,
        *,
        code: str,
        reason: str,
        retryable: bool,
        failure_kind: str = "process",
    ) -> LaunchManifest:
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.LAUNCH_FAILED,
            asserted_by=self.asserted_by,
            timestamp=_now(),
            payload={
                "failure_kind": failure_kind,
                "failure_code": code,
                "reason": reason,
                "retryable": retryable,
            },
        )

    def finish_owned_launch(self) -> LaunchManifest:
        manifest = self.store.manifest(self.identity.launch_id)
        if manifest.lifecycle.is_terminal:
            return manifest
        if manifest.lifecycle.state is LaunchLifecycleState.READY:
            self.store.append(
                self.identity.launch_id,
                event=LaunchEventKind.LAUNCH_STOPPING,
                asserted_by=self.asserted_by,
                timestamp=_now(),
            )
            return self.store.append(
                self.identity.launch_id,
                event=LaunchEventKind.LAUNCH_STOPPED,
                asserted_by=self.asserted_by,
                timestamp=_now(),
            )
        return self.store.append(
            self.identity.launch_id,
            event=LaunchEventKind.LAUNCH_ABORTED,
            asserted_by=self.asserted_by,
            timestamp=_now(),
        )


def contributor_dev_intent() -> LaunchIntent:
    # Compatibility-free authority: contributor command identity is derived from the
    # same resolved plan the supervisor executes.
    from oh_goon.runtime.local_launch_plan import contributor_dev_plan

    root = Path(__file__).resolve().parents[4]
    return contributor_dev_plan(root).intent()


def built_local_intent(*, host: str, port: int) -> LaunchIntent:
    from oh_goon.runtime.local_launch_plan import built_local_plan

    return built_local_plan(host=host, port=port).intent()

def record_backend_startup_complete_from_environment() -> LaunchManifest | None:
    context = LocalLaunchRuntimeContext.from_environment()
    if context is None:
        return None
    owner = LocalLaunchOwner.load(context.launch_id, asserted_by="backend-runtime")
    if owner.identity.owner_generation != context.owner_generation:
        raise ValueError("backend launch context belongs to another owner generation")
    return owner.startup_complete(BACKEND_SERVICE, context.backend_generation)


def _now() -> datetime:
    return datetime.now(UTC)
