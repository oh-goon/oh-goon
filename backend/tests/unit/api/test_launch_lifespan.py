from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from oh_goon.api import app as app_module
from oh_goon.core.launch_manifest import LaunchFactState
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION,
    BACKEND_SERVICE,
    LocalLaunchOwner,
    built_local_intent,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


class FakeRuntime:
    diagnostics = object()

    def start_background(self) -> None:
        pass

    def shutdown(self, *, wait_for_runs: bool = False) -> None:
        assert wait_for_runs is False


def test_lifespan_asserts_startup_only_for_generation_bound_launch_context(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "state" / "oh-goon.db"
    monkeypatch.setenv("OH_GOON_DB", str(database))
    store = LocalLaunchEventStore.configured()
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="cli-start",
        store=store,
    )
    context = owner.install_runtime_context()
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
    monkeypatch.setattr(app_module, "application", lambda: FakeRuntime())

    app = app_module.create_app(local_access_token="test-token")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 204

    manifest = store.manifest(owner.identity.launch_id)
    backend = manifest.observed.services[0]
    assert backend.startup is not None
    assert backend.startup.asserted_by == "backend-runtime"
    assert backend.state("startup") is LaunchFactState.SATISFIED
    assert backend.state("readiness") is LaunchFactState.UNKNOWN
    context.clear()
