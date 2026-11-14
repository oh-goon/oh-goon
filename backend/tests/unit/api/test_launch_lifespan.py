from __future__ import annotations

import os
from pathlib import Path

import pytest
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

    def __init__(self, *, fail_background: bool = False) -> None:
        self.fail_background = fail_background
        self.calls: list[str] = []

    def start_background(self) -> None:
        self.calls.append("background")
        if self.fail_background:
            raise RuntimeError("selected startup failure")

    def shutdown(self, *, wait_for_runs: bool = False) -> None:
        assert wait_for_runs is False
        self.calls.append("shutdown")


def test_lifespan_asserts_startup_only_for_generation_bound_launch_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    runtime = FakeRuntime()
    monkeypatch.setattr(app_module, "application", lambda: runtime)

    app = app_module.create_app(local_access_token="test-token")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 204

    manifest = store.manifest(owner.identity.launch_id)
    backend = manifest.observed.services[0]
    assert backend.startup is not None
    assert backend.startup.asserted_by == "backend-runtime"
    assert backend.state("startup") is LaunchFactState.SATISFIED
    assert backend.state("readiness") is LaunchFactState.UNKNOWN
    assert backend.runtime_process is not None
    assert backend.runtime_process.pid.value == os.getpid()
    assert runtime.calls == ["background", "shutdown"]
    context.clear()


@pytest.mark.parametrize("failure_phase", ["background", "startup-report"])
def test_startup_failure_closes_the_original_runtime(
    monkeypatch: pytest.MonkeyPatch, failure_phase: str
) -> None:
    runtime = FakeRuntime(fail_background=failure_phase == "background")
    monkeypatch.setattr(app_module, "application", lambda: runtime)

    def startup_report() -> None:
        runtime.calls.append("startup-report")
        raise RuntimeError("selected startup failure")

    monkeypatch.setattr(
        app_module, "record_backend_startup_complete_from_environment", startup_report
    )
    app = app_module.create_app(local_access_token="test-token")
    with pytest.raises(RuntimeError, match="selected startup failure"):
        with TestClient(app):
            pytest.fail("A failed startup cannot serve requests")
    assert runtime.calls == (
        ["background", "shutdown"]
        if failure_phase == "background"
        else ["background", "startup-report", "shutdown"]
    )
