"""A real loopback peer cannot extend cancellation by trickling HTTP bytes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import cast

import pytest
from tests.factories.capabilities import capability_snapshot

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_run_cancel import run_cancel_command
from oh_goon.core.models.run import (
    CancelledFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    QueuedFleetRun,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_launch import ManagedApplicationLaunchReceipt
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.runtime.database import ExistingDatabaseAppendContext


@contextmanager
def _native_cancellation_peer(mode: str, body: bytes) -> Iterator[tuple[str, list[bytes]]]:
    """Own a finite TCP peer, including thread/socket cleanup after a cancelled read."""
    stop = threading.Event()
    requests: list[bytes] = []
    errors: list[Exception] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(0.1)
        host, port = listener.getsockname()

        def serve() -> None:
            while not stop.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(0.5)
                    try:
                        raw = bytearray()
                        while b"\r\n\r\n" not in raw:
                            chunk = connection.recv(4096)
                            if not chunk:
                                raise ValueError("Missing cancellation request headers")
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                raise ValueError("Cancellation request is unexpectedly large")
                        headers, request_body = bytes(raw).split(b"\r\n\r\n", 1)
                        length = next(
                            int(line.partition(b":")[2])
                            for line in headers.split(b"\r\n")[1:]
                            if line.lower().startswith(b"content-length:")
                        )
                        while len(request_body) < length:
                            chunk = connection.recv(4096)
                            if not chunk:
                                raise ValueError("Missing cancellation request body")
                            request_body += chunk
                            if len(request_body) > 65536:
                                raise ValueError("Cancellation request is unexpectedly large")
                        requests.append(headers + b"\r\n\r\n" + request_body)
                        response_headers = (
                            b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n"
                            + f"Content-Length: {len(body)}\r\n".encode()
                            + b"Connection: close\r\n\r\n"
                        )
                        if mode == "immediate":
                            connection.sendall(response_headers + body)
                        else:
                            if mode == "body":
                                connection.sendall(response_headers)
                                trickle = body
                            else:
                                trickle = response_headers + body
                            for byte in trickle:
                                if stop.wait(0.025):
                                    break
                                connection.sendall(bytes([byte]))
                    except (BrokenPipeError, ConnectionResetError):
                        # A deadline closes this exact client before the response ends.
                        pass
                    except Exception as error:
                        errors.append(error)

        thread = threading.Thread(target=serve, name="cancellation-http-peer")
        thread.start()
        try:
            yield f"http://{host}:{port}", requests
        finally:
            stop.set()
            thread.join(timeout=2)
            assert not thread.is_alive(), "The owned HTTP peer did not stop"
            assert not errors, errors


@pytest.mark.parametrize("mode", ["immediate", "headers", "body"])
def test_native_http_deadline_covers_header_and_body_trickles_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    now = utc_now()
    receipt = ManagedApplicationLaunchReceipt(
        launch_id="transport-launch",
        owner_generation=1,
        backend_generation=1,
        profile="built-local",
        resolved_intent_identity="sha256:" + "a" * 64,
        backend_service_generation=1,
        backend_pid=os.getpid(),
        backend_birth_provider="linux-proc",
        backend_boot_id="transport-boot",
        backend_start_ticks=1,
    )
    detail = FleetRunDetail(
        run=QueuedFleetRun(
            fleet_run_id="selected-run",
            capability=capability_snapshot(OperationAction.INIT),
            repository_scope=fleet_repository_scope(None),
            targets=(),
            accepted_at=now,
            progress=FleetRunProgress(total=0, succeeded=0, failed=0),
            status=FleetRunStatus.QUEUED,
        ),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
        application_launch=receipt,
    )
    cancelled = detail.model_copy(
        update={
            "run": CancelledFleetRun.model_validate(
                {
                    **detail.run.model_dump(exclude_computed_fields=True),
                    "status": FleetRunStatus.CANCELLED,
                    "cancel_requested_at": now,
                    "finished_at": now,
                }
            )
        }
    )
    state = ExistingDatabaseAppendContext(
        path=tmp_path / "state.db",
        device=0,
        inode=1,
        schema_cookie=1,
        database_identity_cookie=1,
        journal_mode="DELETE",
        durable_revision=0,
        session_id="transport-observer",
    )
    reads: list[str] = []

    def observe(run_id: str) -> tuple[FleetRunDetail, ExistingDatabaseAppendContext]:
        reads.append(run_id)
        return detail, state

    def readback(run_id: str) -> FleetRunDetail:
        reads.append(run_id)
        return cancelled

    runtime = cast(
        ApplicationRuntime,
        SimpleNamespace(runs=SimpleNamespace(cancellation_observation=observe, get=readback)),
    )
    token = "PRIVATE_TRANSPORT_CAPABILITY_CANARY"
    monkeypatch.setattr("oh_goon.cli_run_cancel._REQUEST_TIMEOUT_SECONDS", 0.3)
    with _native_cancellation_peer(mode, cancelled.run.model_dump_json().encode()) as (
        endpoint,
        requests,
    ):
        monkeypatch.setattr(
            "oh_goon.cli_run_cancel._control_target", lambda _receipt, _state: (endpoint, token)
        )
        started = monotonic()
        exit_code = run_cancel_command(
            runtime, argparse.Namespace(fleet_run_id="selected-run", as_json=True)
        )
        elapsed = monotonic() - started
        assert elapsed < 1.5, "A peer extended the total deadline by making partial progress"
        assert len(requests) == 1
        headers, request_body = requests[0].split(b"\r\n\r\n", 1)
        assert headers.startswith(b"POST /api/fleet/runs/selected-run/cancel HTTP/1.1\r\n")
        assert token.encode() in headers
        assert json.loads(request_body)["application_launch"]["launch_id"] == receipt.launch_id
    output = capsys.readouterr().out
    result = json.loads(output)
    assert token not in output
    assert result["request_attempted"] is True
    assert result["cleanup"] == "NOT_ESTABLISHED_BY_CANCELLATION_REQUEST"
    if mode == "immediate":
        assert exit_code == 0 and result["cancellation_requested"] is True
        assert result["code"] == "CANCELLATION_REQUESTED"
        assert reads == ["selected-run", "selected-run"]
    else:
        assert exit_code == 2 and result["cancellation_requested"] is None
        assert result["code"] == "CONTROL_REQUEST_UNCONFIRMED"
        assert reads == ["selected-run"]
