"""Send one cancellation request to the original proven live application owner."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
from pathlib import Path
from urllib.parse import quote

import httpx2

from oh_goon.api.local_access import LOCAL_ACCESS_HEADER
from oh_goon.api.schemas.fleet import FleetRunCancellationRequest
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.launch_manifest import LaunchFactState, LaunchLifecycleState
from oh_goon.core.launch_status import LaunchOwnerProof, safe_launch_status
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_launch import ManagedApplicationLaunchReceipt
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.core.vocabulary import FleetRunStatus
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

_SCHEMA = "oh-goon.run-cancel/v1"
_RESPONSE_BYTE_LIMIT = 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 5.0
_SAFE_REFUSAL_CODES = frozenset(
    {
        "CONTROL_OWNER_UNAVAILABLE",
        "INVALID_REQUEST",
        "INVALID_RUN_TRANSITION",
        "LOCAL_ACCESS_REQUIRED",
        "RUN_NOT_FOUND",
        "STATE_CONFLICT",
    }
)


class _CancellationRefused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _control_target(receipt: ManagedApplicationLaunchReceipt, state_path: Path) -> tuple[str, str]:
    """Resolve an exact recorded backend; neither workspace discovery nor URL input."""
    manifest = LocalLaunchEventStore.from_database(state_path).manifest(receipt.launch_id)
    if (
        manifest.identity.launch_id != receipt.launch_id
        or manifest.identity.owner_generation != receipt.owner_generation
        or manifest.intent.profile != receipt.profile
        or manifest.intent.identity() != receipt.resolved_intent_identity
        or manifest.lifecycle.state
        not in {LaunchLifecycleState.READY, LaunchLifecycleState.DEGRADED}
        or safe_launch_status(manifest).owner_proof is not LaunchOwnerProof.AVAILABLE
    ):
        raise ValueError("Original launch owner is unavailable")
    requested = next(
        (service for service in manifest.intent.services if service.service == "backend"), None
    )
    backend = next(
        (service for service in manifest.observed.services if service.service == "backend"), None
    )
    birth = ProcessBirthIdentity(
        pid=receipt.backend_pid,
        boot_id=receipt.backend_boot_id,
        start_ticks=receipt.backend_start_ticks,
        provider=receipt.backend_birth_provider,
    )
    if (
        requested is None
        or requested.endpoint is None
        or requested.endpoint.exposure != "local"
        or backend is None
        or backend.service_generation != receipt.backend_generation
        or backend.service_generation != receipt.backend_service_generation
        or backend.state("startup") is not LaunchFactState.SATISFIED
        or backend.state("readiness") is not LaunchFactState.SATISFIED
        or backend.runtime_process is None
        or backend.runtime_process.birth_identity is None
        or backend.runtime_process.birth_identity.value != birth
        or observe_process_birth(birth.pid) != birth
        or backend.bound_endpoint is None
        or backend.bound_endpoint.host.value != receipt.observed_host
        or backend.bound_endpoint.port.value != receipt.observed_port
    ):
        raise ValueError("Original backend owner is unavailable")
    host = backend.bound_endpoint.host.value
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Original backend endpoint is not numeric loopback")
    rendered_host = f"[{host}]" if ":" in host else host
    endpoint = f"http://{rendered_host}:{backend.bound_endpoint.port.value}"
    token = LocalLaunchAccessStore.from_database(state_path).token(
        launch_id=receipt.launch_id, owner_generation=receipt.owner_generation
    )
    return endpoint, token


def _post_cancellation(
    endpoint: str, token: str, run_id: str, request: FleetRunCancellationRequest
) -> tuple[str, FleetRunStatus]:
    """Perform exactly one bounded native HTTP request without proxy, redirect or retry."""
    return asyncio.run(_send_cancellation(endpoint, token, run_id, request))


async def _send_cancellation(
    endpoint: str, token: str, run_id: str, request: FleetRunCancellationRequest
) -> tuple[str, FleetRunStatus]:
    # Native timeout cancellation spans connect, headers and raw body reads, so
    # an endpoint dripping bytes cannot restart the overall request deadline.
    async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
        async with httpx2.AsyncClient(
            transport=httpx2.AsyncHTTPTransport(retries=0, trust_env=False),
            trust_env=False,
            follow_redirects=False,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        ) as client:
            async with client.stream(
                "POST",
                f"{endpoint}/api/fleet/runs/{quote(run_id, safe='')}/cancel",
                headers={LOCAL_ACCESS_HEADER: token, "Accept-Encoding": "identity"},
                json=request.model_dump(mode="json"),
            ) as response:
                if response.headers.get("Content-Encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise ValueError("Cancellation response must use identity content encoding")
                declared_size = response.headers.get("Content-Length", "")
                if declared_size.isdecimal() and int(declared_size) > _RESPONSE_BYTE_LIMIT:
                    raise ValueError("Cancellation response exceeds its byte limit")
                payload = bytearray()
                async for chunk in response.aiter_raw():
                    if len(payload) + len(chunk) > _RESPONSE_BYTE_LIMIT:
                        raise ValueError("Cancellation response exceeds its byte limit")
                    payload.extend(chunk)
                decoded = json.loads(payload)
                if response.status_code != 202:
                    code = decoded.get("code") if isinstance(decoded, dict) else None
                    if response.status_code in {400, 401, 403, 404, 409, 422} and (
                        isinstance(code, str) and code in _SAFE_REFUSAL_CODES
                    ):
                        raise _CancellationRefused(code)
                    raise ValueError("Cancellation response is not a canonical acknowledgement")
                if (
                    not isinstance(decoded, dict)
                    or not isinstance(decoded.get("fleet_run_id"), str)
                    or not isinstance(decoded.get("status"), str)
                ):
                    raise ValueError("Cancellation response lacks canonical identity/status")
                return decoded["fleet_run_id"], FleetRunStatus(decoded["status"])


def _print_result(
    args: argparse.Namespace,
    *,
    code: str,
    requested: bool | None,
    attempted: bool,
    detail: FleetRunDetail | None,
) -> int:
    run_id = args.fleet_run_id
    guidance = (
        "Inspect the original Run handoff for terminal and physical cleanup evidence."
        if requested
        else "Inspect the exact Run in this state root. If its original standalone CLI is "
        "still active, use Ctrl-C in that terminal. An unconfirmed request must be inspected "
        "before any explicit retry."
    )
    result = {
        "schema": _SCHEMA,
        "fleet_run_id": run_id,
        "code": code,
        "cancellation_requested": requested,
        "request_attempted": attempted,
        "execution_status": detail.run.status.value if detail is not None else None,
        "execution_terminal": detail.run.status.terminal if detail is not None else None,
        "cleanup": "NOT_ESTABLISHED_BY_CANCELLATION_REQUEST",
        "readback_argv": ["oh-goon", "runs", "handoff", run_id, "--json"],
        "safe_next_action": guidance,
    }
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Run cancellation: {code}")
        print(f"Fleet Run ID: {run_id}")
        print(f"Execution status: {result['execution_status'] or 'unobserved'}")
        print("Cleanup: not established by this cancellation request")
        print(guidance)
        print("Readback argv: " + json.dumps(result["readback_argv"]))
    return 0 if requested is True else 2


def run_cancel_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Observe first; all mutation stays with the original live Fleet cancellation owner."""
    detail = None
    try:
        detail, state = runtime.runs.cancellation_observation(args.fleet_run_id)
        if detail.run.status.terminal:
            raise _CancellationRefused("INVALID_RUN_TRANSITION")
        receipt = detail.application_launch
        if not isinstance(receipt, ManagedApplicationLaunchReceipt):
            raise _CancellationRefused("CONTROL_OWNER_UNAVAILABLE")
        endpoint, token = _control_target(receipt, state.path)
        request = FleetRunCancellationRequest(application_launch=receipt, state=state)
    except FleetDomainError as error:
        return _print_result(args, code=error.code, requested=False, attempted=False, detail=detail)
    except _CancellationRefused as error:
        return _print_result(args, code=error.code, requested=False, attempted=False, detail=detail)
    except (OSError, ValueError):
        return _print_result(
            args,
            code="CONTROL_OWNER_UNAVAILABLE",
            requested=False,
            attempted=False,
            detail=detail,
        )
    try:
        acknowledged_id, acknowledged_status = _post_cancellation(
            endpoint, token, args.fleet_run_id, request
        )
        if acknowledged_id != args.fleet_run_id or not acknowledged_status.cancel_request_satisfied:
            raise ValueError("Cancellation acknowledgement does not match the selected Run")
        observed = runtime.runs.get(args.fleet_run_id)
        if (
            observed.application_launch != receipt
            or not observed.run.status.cancel_request_satisfied
        ):
            raise ValueError("Durable cancellation readback is not confirmed")
    except _CancellationRefused as error:
        return _print_result(args, code=error.code, requested=False, attempted=True, detail=detail)
    except (httpx2.HTTPError, OSError, ValueError, RecursionError, FleetDomainError):
        return _print_result(
            args,
            code="CONTROL_REQUEST_UNCONFIRMED",
            requested=None,
            attempted=True,
            detail=None,
        )
    return _print_result(
        args,
        code="CANCELLATION_REQUESTED",
        requested=True,
        attempted=True,
        detail=observed,
    )
