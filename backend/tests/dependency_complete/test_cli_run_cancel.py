"""Exact Run cancellation is a bounded request to its original live application."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx2
import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_parser import build_parser
from oh_goon.cli_run_cancel import run_cancel_command
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.core.errors import RunNotFound
from oh_goon.core.launch_manifest import LaunchEndpointIntent, LaunchIntent, LaunchServiceIntent
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CancelledFleetRun,
    CancellingFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    FleetRunState,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.run_launch import (
    ManagedApplicationLaunchReceipt,
    UnmanagedApplicationLaunchReceipt,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.runtime.database import ExistingDatabaseAppendContext, LocalDatabase
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_admission import repository_admissions_for


class _OriginalObservation:
    def __init__(self, detail: FleetRunDetail, state: ExistingDatabaseAppendContext) -> None:
        self.detail = detail
        self.state = state
        self.reads: list[str] = []

    def cancellation_observation(
        self, run_id: str
    ) -> tuple[FleetRunDetail, ExistingDatabaseAppendContext]:
        self.reads.append(run_id)
        if run_id != self.detail.run.fleet_run_id:
            raise RunNotFound("PRIVATE_UNKNOWN_RUN_CANARY")
        return self.detail, self.state

    def get(self, run_id: str) -> FleetRunDetail:
        self.reads.append(run_id)
        assert run_id == self.detail.run.fleet_run_id
        return self.detail

    def cancel(self, run_id: str) -> None:
        pytest.fail("A fresh observation runtime cannot perform cancellation")


@dataclass
class _Fixture:
    observer: _OriginalObservation
    owner: LocalLaunchOwner
    token: str

    @property
    def runtime(self) -> ApplicationRuntime:
        return cast(ApplicationRuntime, SimpleNamespace(runs=self.observer))


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    database_path = tmp_path / "state.db"
    monkeypatch.setenv("OH_GOON_DB", str(database_path))
    database = LocalDatabase(database_path)
    with database.transaction() as connection:
        state = database.capture_append_context(connection)
    owner = LocalLaunchOwner.create(
        intent=LaunchIntent(
            profile="built-local",
            services=(
                LaunchServiceIntent(
                    service="backend",
                    command=("uvicorn", "PRIVATE_ARGV_CANARY"),
                    endpoint=LaunchEndpointIntent("backend", "127.0.0.1", 8787),
                ),
            ),
        ),
        workspace=tmp_path,
        asserted_by="unit-native-launch-owner",
        store=LocalLaunchEventStore.from_database(database_path),
    )
    owner.spawned("backend", 1, os.getpid())
    owner.bound("backend", 1, host="127.0.0.1", port=8787)
    owner.startup_complete("backend", 1)
    owner.ready("backend", 1)
    owner.launch_ready()
    context: dict[str, str] = {}
    owner.install_runtime_context(environ=context)
    for name, value in context.items():
        monkeypatch.setenv(name, value)
    receipt = current_application_launch_receipt()
    assert isinstance(receipt, ManagedApplicationLaunchReceipt)
    token = "PRIVATE_LOCAL_ACCESS_CANARY"
    LocalLaunchAccessStore.from_database(database_path).publish(
        launch_id=receipt.launch_id, owner_generation=receipt.owner_generation, token=token
    )
    repository = RepositoryIdentity.model_validate({"path": tmp_path, "name": "selected"})
    target = structural_fleet_plan_target(
        ResolvedFleetPlan.create(
            name="bounded",
            description="",
            execution=VerificationExecutionPolicy(),
            steps=(ResolvedCommandStep(step_id="bounded", argv=("sleep", "5")),),
        )
    )
    now = utc_now()
    detail = FleetRunDetail(
        run=RunningFleetRun(
            fleet_run_id="selected-run",
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=fleet_repository_scope({repository.repository_id}),
            targets=(repository,),
            accepted_at=now,
            started_at=now,
            progress=FleetRunProgress(total=1, succeeded=0, failed=0),
            status=FleetRunStatus.RUNNING,
        ),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, [repository]),
        application_launch=receipt,
    )
    return _Fixture(_OriginalObservation(detail, state), owner, token)


def _invoke(fixture: _Fixture, *, as_json: bool = True, run_id: str = "selected-run") -> int:
    return run_cancel_command(
        fixture.runtime,
        argparse.Namespace(fleet_run_id=run_id, as_json=as_json),
    )


def _cancelled(detail: FleetRunDetail, *, terminal: bool = False) -> FleetRunDetail:
    values = detail.run.model_dump(exclude={"status"}, exclude_computed_fields=True)
    values["cancel_requested_at"] = utc_now()
    run: FleetRunState
    if terminal:
        values["finished_at"] = utc_now()
        run = CancelledFleetRun.model_validate({**values, "status": FleetRunStatus.CANCELLED})
    else:
        run = CancellingFleetRun.model_validate({**values, "status": FleetRunStatus.CANCELLING})
    return detail.model_copy(update={"run": run})


def _transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> None:
    def streamed(request: httpx2.Request) -> httpx2.Response:
        response = handler(request)
        if response.is_stream_consumed:
            return httpx2.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx2.ByteStream(response.content),
            )
        return response

    def transport(*, retries: int, trust_env: bool) -> httpx2.AsyncBaseTransport:
        assert retries == 0 and trust_env is False
        return httpx2.MockTransport(streamed)

    monkeypatch.setattr("oh_goon.cli_run_cancel.httpx2.AsyncHTTPTransport", transport)


@pytest.mark.parametrize("terminal", [False, True], ids=["requested", "settled-before-readback"])
@pytest.mark.parametrize("as_json", [False, True], ids=["human", "json"])
def test_original_owner_acknowledgement_requires_fresh_durable_cancellation(
    managed: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    terminal: bool,
    as_json: bool,
) -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:8787/api/fleet/runs/selected-run/cancel"
        assert request.headers["X-Oh-Goon-Local-Access"] == managed.token
        payload = json.loads(request.content)
        assert payload[
            "application_launch"
        ] == managed.observer.detail.application_launch.model_dump(mode="json")
        assert payload["state"] == managed.observer.state.model_dump(mode="json")
        acknowledged = _cancelled(managed.observer.detail)
        managed.observer.detail = _cancelled(managed.observer.detail, terminal=terminal)
        return httpx2.Response(202, json=acknowledged.run.model_dump(mode="json"))

    _transport(monkeypatch, handler)
    args = build_parser().parse_args(
        ["runs", "cancel", "selected-run", *(["--json"] if as_json else [])]
    )
    assert run_plan_command(managed.runtime, args) == 0
    output = capsys.readouterr().out
    assert len(calls) == 1 and managed.observer.reads == ["selected-run", "selected-run"]
    assert managed.token not in output and "PRIVATE_ARGV_CANARY" not in output
    if as_json:
        result = json.loads(output)
        assert result["schema"] == "oh-goon.run-cancel/v1"
        assert result["cancellation_requested"] is True
        assert result["request_attempted"] is True
        assert result["execution_status"] == ("CANCELLED" if terminal else "CANCELLING")
        assert result["execution_terminal"] is terminal
        assert result["cleanup"] == "NOT_ESTABLISHED_BY_CANCELLATION_REQUEST"
    else:
        assert "CANCELLATION_REQUESTED" in output and "not established" in output


@pytest.mark.parametrize(
    "reason", ["unknown", "terminal", "unmanaged", "capability-absent", "dead-backend"]
)
def test_unproven_control_refuses_before_http_without_fresh_controller(
    managed: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reason: str,
) -> None:
    def forbidden(**options: object) -> httpx2.AsyncClient:
        pytest.fail("No request is authorized")

    monkeypatch.setattr("oh_goon.cli_run_cancel.httpx2.AsyncClient", forbidden)
    run_id = "unknown-run" if reason == "unknown" else "selected-run"
    if reason == "terminal":
        managed.observer.detail = _cancelled(managed.observer.detail, terminal=True)
    elif reason == "unmanaged":
        managed.observer.detail = managed.observer.detail.model_copy(
            update={"application_launch": UnmanagedApplicationLaunchReceipt()}
        )
    elif reason == "capability-absent":
        LocalLaunchAccessStore.from_database(managed.observer.state.path).path(
            managed.owner.identity.launch_id
        ).unlink()
    elif reason == "dead-backend":
        monkeypatch.setattr("oh_goon.cli_run_cancel.observe_process_birth", lambda pid: None)
    assert _invoke(managed, run_id=run_id) == 2
    result = json.loads(capsys.readouterr().out)
    expected = {"unknown": "RUN_NOT_FOUND", "terminal": "INVALID_RUN_TRANSITION"}.get(
        reason, "CONTROL_OWNER_UNAVAILABLE"
    )
    assert result["code"] == expected and result["request_attempted"] is False
    assert result["cancellation_requested"] is False
    assert "PRIVATE_UNKNOWN_RUN_CANARY" not in json.dumps(result)
    assert managed.observer.reads == [run_id]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("owner_generation", 2),
        ("backend_generation", 2),
        ("backend_service_generation", 2),
        ("backend_start_ticks", 0),
        ("resolved_intent_identity", "sha256:" + "f" * 64),
        ("observed_port", 8888),
    ],
)
def test_changed_recorded_owner_or_generation_cannot_redirect_request(
    managed: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    field: str,
    replacement: object,
) -> None:
    receipt = managed.observer.detail.application_launch.model_copy(update={field: replacement})
    managed.observer.detail = managed.observer.detail.model_copy(
        update={"application_launch": receipt}
    )

    def forbidden(**options: object) -> httpx2.AsyncClient:
        pytest.fail("Changed authority cannot make a network request")

    monkeypatch.setattr("oh_goon.cli_run_cancel.httpx2.AsyncClient", forbidden)
    assert _invoke(managed) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "CONTROL_OWNER_UNAVAILABLE" and not result["request_attempted"]


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "redirect",
        "oversized",
        "oversized-declared",
        "encoded",
        "invalid-json",
        "deep-json",
        "unrecognized-status",
        "unexpected-http-success",
        "wrong-run",
        "stale-readback",
        "readback-unavailable",
    ],
)
def test_ambiguous_delivery_never_resends_or_claims_cancellation(
    managed: _Fixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if failure == "timeout":
            raise httpx2.ReadTimeout("PRIVATE_TRANSPORT_CANARY", request=request)
        if failure == "redirect":
            return httpx2.Response(307, headers={"Location": "https://private.invalid/"})
        if failure == "oversized":
            return httpx2.Response(202, content=b" " * (1024 * 1024 + 1))
        if failure == "oversized-declared":
            return httpx2.Response(
                202,
                headers={"Content-Length": str(1024 * 1024 + 1)},
                stream=httpx2.ByteStream(b"{}"),
            )
        if failure == "encoded":
            return httpx2.Response(
                202,
                headers={"Content-Encoding": "gzip"},
                stream=httpx2.ByteStream(b"PRIVATE_ENCODED_CANARY"),
            )
        if failure == "invalid-json":
            return httpx2.Response(202, content=b"PRIVATE_RESPONSE_CANARY")
        if failure == "deep-json":
            return httpx2.Response(202, content=b"[" * 2000 + b"]" * 2000)
        acknowledged = _cancelled(managed.observer.detail)
        if failure == "wrong-run":
            acknowledged = acknowledged.model_copy(
                update={"run": acknowledged.run.model_copy(update={"fleet_run_id": "foreign-run"})}
            )
        payload = acknowledged.run.model_dump(mode="json")
        if failure == "unrecognized-status":
            payload["status"] = "PRIVATE_NATIVE_STATUS_CANARY"
        return httpx2.Response(200 if failure == "unexpected-http-success" else 202, json=payload)

    if failure == "readback-unavailable":

        def unavailable(run_id: str) -> FleetRunDetail:
            raise RunNotFound("PRIVATE_READBACK_CANARY")

        monkeypatch.setattr(managed.observer, "get", unavailable)
    _transport(monkeypatch, handler)
    assert _invoke(managed) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert len(calls) == 1
    assert result["code"] == "CONTROL_REQUEST_UNCONFIRMED"
    assert result["cancellation_requested"] is None and result["request_attempted"] is True
    assert result["execution_status"] is None and result["execution_terminal"] is None
    assert "PRIVATE_" not in output


def test_original_owner_refusal_does_not_become_success_or_expose_server_prose(
    managed: _Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(409, json={"code": "STATE_CONFLICT", "message": "PRIVATE_CANARY"})

    _transport(monkeypatch, handler)
    assert _invoke(managed) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["code"] == "STATE_CONFLICT"
    assert result["cancellation_requested"] is False and result["request_attempted"] is True
    assert "PRIVATE_CANARY" not in output


@pytest.mark.parametrize(
    "tail",
    [
        [],
        ["--last"],
        ["selected-run", "--yes"],
        ["selected-run", "--endpoint", "http://127.0.0.1"],
        ["selected-run", "--token", "canary"],
    ],
)
def test_parser_requires_an_explicit_run_without_alternate_authority(tail: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["runs", "cancel", *tail])
