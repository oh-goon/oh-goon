"""Installed readiness only; no real native stores, sessions or model inference."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    run_command,
)
from oh_goon.integrations.agent_opencode_service import (
    OpenCodeServiceClient,
    OpenCodeServiceError,
    OpenCodeServiceMetadata,
)


@pytest.mark.certification
@pytest.mark.skipif(sys.platform != "linux", reason="Installed Linux service qualification")
def test_installed_opencode_readiness_preserves_native_service_until_owner_cancels(
    tmp_path: Path,
) -> None:
    asyncio.run(_installed_readiness(tmp_path))


async def _installed_readiness(tmp_path: Path) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_OPENCODE")
    if not executable:
        pytest.skip("Select the installed public OpenCode executable explicitly")
    program = Path(executable).resolve(strict=True)
    assert program.is_file()
    directories = {
        name: tmp_path / name for name in ("home", "config", "data", "state", "cache", "work")
    }
    for directory in directories.values():
        directory.mkdir(mode=0o700)
    with socket.socket() as selection:
        selection.bind(("127.0.0.1", 0))
        port = selection.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    password = SecretStr(secrets.token_urlsafe(32))
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directories["home"]),
        "XDG_CONFIG_HOME": str(directories["config"]),
        "XDG_DATA_HOME": str(directories["data"]),
        "XDG_STATE_HOME": str(directories["state"]),
        "XDG_CACHE_HOME": str(directories["cache"]),
        "OPENCODE_CONFIG_DIR": str(directories["config"]),
        "OPENCODE_PASSWORD": password.get_secret_value(),
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_CONFIG_PROJECT_DISABLE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    }
    cancellation = threading.Event()
    process_started = threading.Event()
    events: list[CommandProcessLifecycleEvent] = []
    results: list[CommandResult | BaseException] = []

    def observe(event: CommandProcessLifecycleEvent) -> None:
        events.append(event)
        if event.kind == "PROCESS_STARTED":
            process_started.set()

    def execute() -> None:
        try:
            results.append(
                run_command(
                    str(program),
                    "serve",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    cwd=directories["work"],
                    env=environment,
                    timeout=25,
                    cancel_requested=cancellation.is_set,
                    process_observer=observe,
                    require_process_group_quiescence=True,
                    max_capture_bytes_per_stream=65536,
                    retain_output_tail=True,
                )
            )
        except BaseException as error:
            results.append(error)

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    metadata: OpenCodeServiceMetadata | None = None
    try:
        assert process_started.wait(timeout=3), (
            "Original command owner did not report process birth"
        )
        deadline = time.monotonic() + 15
        while worker.is_alive() and time.monotonic() < deadline:
            try:
                async with OpenCodeServiceClient(
                    endpoint, password=password, timeout_seconds=0.5
                ) as client:
                    metadata = client.metadata
                    with pytest.raises(
                        CapabilityUnavailable, match="store exclusion are unqualified"
                    ):
                        client.require_protected_execution()
                break
            except OpenCodeServiceError:
                await asyncio.sleep(0.05)
        assert metadata is not None, "Selected owned native service did not establish V2 readiness"
        births = [event for event in events if event.kind == "PROCESS_STARTED"]
        assert len(births) == 1
        assert metadata == OpenCodeServiceMetadata("2.1.2", births[0].pid)
        assert worker.is_alive(), "Closing the client must not shut down the native service"
        async with OpenCodeServiceClient(
            endpoint, password=password, timeout_seconds=1
        ) as reopened:
            assert reopened.metadata == metadata
    finally:
        cancellation.set()
        worker.join(timeout=10)
    assert not worker.is_alive(), "Original command owner did not close the native process"
    assert len(results) == 1 and isinstance(results[0], CommandCancelledError)
    assert results[0].supervisor.final_process_group_quiescent is True
    assert any(event.kind == "DESCENDANTS_QUIESCENT" for event in events)
