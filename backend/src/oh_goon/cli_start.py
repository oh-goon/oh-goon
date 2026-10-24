from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import ModuleType

from oh_goon.api.local_access import ensure_local_access_token
from oh_goon.api.ui import built_ui_available
from oh_goon.cli_local_state import check_local_state_for_startup
from oh_goon.core.errors import LeaguePreferencesInvalid
from oh_goon.core.launch_manifest import LaunchIntent
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION,
    BACKEND_SERVICE,
    LocalLaunchOwner,
    built_local_intent,
)
from oh_goon.runtime.local_launch_plan import built_local_plan
from oh_goon.runtime.local_launch_reuse import (
    LocalLaunchReuseAction,
    LocalLaunchReuseDecision,
    assess_local_launch_reuse,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.local_launch_supervisor import start_inprocess_readiness_monitor

LOCAL_APP_HOST = "127.0.0.1"
LOCAL_APP_PORT = 8787


def _render_reuse_decision(*, workspace: Path, decision: LocalLaunchReuseDecision) -> int | None:
    if decision.action is LocalLaunchReuseAction.CONFLICT:
        print("OH-GOON LAUNCH CONFLICT")
        print(f"Workspace: {workspace}")
        if decision.launch_id is not None:
            print(f"Current launch: {decision.launch_id}")
        print(f"Reason: {decision.reason}")
        print("Inspect: oh-goon status")
        return 2
    if decision.action is not LocalLaunchReuseAction.REUSE:
        return None
    assert decision.status is not None and decision.launch_id is not None
    print("oh-goon local rink")
    print(f"Launch:         {decision.launch_id}")
    print(f"Lifecycle:      {decision.status.lifecycle.value}")
    print("Reuse:          existing compatible live owner")
    if decision.status.lifecycle.value == "ready":
        print("Open:           oh-goon open")
    else:
        print("Status:         oh-goon status")
    return 0


def run_start_command(args: argparse.Namespace) -> int | None:
    if args.command != "start":
        return None
    if check_local_state_for_startup() != 0:
        return 2

    intent = built_local_intent(host=LOCAL_APP_HOST, port=LOCAL_APP_PORT)
    workspace = Path.cwd().resolve()
    events = LocalLaunchEventStore.configured()
    bindings = LocalLaunchBindingStore.from_event_store(events)
    decision = assess_local_launch_reuse(
        workspace=workspace,
        intent=intent,
        binding_store=bindings,
        event_store=events,
    )
    reuse_result = _render_reuse_decision(workspace=workspace, decision=decision)
    if reuse_result is not None:
        return reuse_result
    import uvicorn

    loaded = _load_league_folders()
    if loaded is None:
        return 2
    folders, saved = loaded
    owner, launch_context = _create_local_launch(
        intent=intent,
        workspace=workspace,
        events=events,
        bindings=bindings,
        folders=folders,
        saved=saved,
    )
    return _run_local_server(owner, launch_context, uvicorn)


def _load_league_folders() -> tuple[LeagueFolderStore, tuple[Path, ...]] | None:
    if not built_ui_available():
        print("The bundled oh-goon UI is not present in this source checkout/install.")
        print("For contributor development use: make dev")
        print("For normal offline/local use install a built oh-goon release and run: oh-goon start")
        return None
    folders = LeagueFolderStore()
    try:
        saved = folders.list()
    except LeaguePreferencesInvalid as exc:
        print(str(exc))
        print(f"League preferences: {folders.path}")
        return None
    return folders, saved


def _create_local_launch(
    *,
    intent: LaunchIntent,
    workspace: Path,
    events: LocalLaunchEventStore,
    bindings: LocalLaunchBindingStore,
    folders: LeagueFolderStore,
    saved: tuple[Path, ...],
) -> tuple[LocalLaunchOwner, LocalLaunchRuntimeContext]:
    owner = LocalLaunchOwner.create(
        intent=intent,
        workspace=workspace,
        asserted_by="cli-start",
        store=events,
        binding_store=bindings,
    )
    token = ensure_local_access_token()
    LocalLaunchAccessStore.configured().publish(
        launch_id=owner.identity.launch_id,
        owner_generation=owner.identity.owner_generation,
        token=token,
    )
    launch_context = owner.install_runtime_context(backend_generation=BACKEND_GENERATION)
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
    print("oh-goon local rink")
    print(f"League folders: {len(saved)} saved on this computer")
    print(f"League file:    {folders.path}")
    print(f"Launch:         {owner.identity.launch_id}")
    print("Stop:           Ctrl+C")
    return owner, launch_context


def _run_local_server(
    owner: LocalLaunchOwner, launch_context: LocalLaunchRuntimeContext, uvicorn: ModuleType
) -> int:
    plan = built_local_plan(host=LOCAL_APP_HOST, port=LOCAL_APP_PORT)
    token = LocalLaunchAccessStore.configured().token(
        launch_id=owner.identity.launch_id,
        owner_generation=owner.identity.owner_generation,
    )
    config = uvicorn.Config(
        "oh_goon.api.app:app", host=LOCAL_APP_HOST, port=LOCAL_APP_PORT, reload=False
    )
    server = uvicorn.Server(config)
    monitor = start_inprocess_readiness_monitor(
        owner=owner,
        plan=plan,
        on_ready=lambda: print(
            f"Open:           http://{LOCAL_APP_HOST}:{LOCAL_APP_PORT}/#access={token}"
        ),
        on_failure=lambda: setattr(server, "should_exit", True),
    )
    try:
        try:
            server.run()
        except KeyboardInterrupt:
            owner.finish_owned_launch()
            return 0
        except BaseException as exc:
            manifest = owner.store.manifest(owner.identity.launch_id)
            if not manifest.lifecycle.is_terminal:
                owner.service_failed(
                    BACKEND_SERVICE,
                    BACKEND_GENERATION,
                    code="uvicorn_exit",
                    reason=f"uvicorn terminated before normal shutdown: {type(exc).__name__}",
                    retryable=True,
                )
                owner.launch_failed(
                    code="local_server_exit",
                    reason=f"local server terminated before normal shutdown: {type(exc).__name__}",
                    retryable=True,
                )
            raise
        owner.finish_owned_launch()
        return 0
    finally:
        monitor.cancel()
        launch_context.clear()
