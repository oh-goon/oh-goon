from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from oh_goon.core.errors import (
    ExecutionAuthorityDivergence,
    ExecutorPlacementLost,
    FleetPlanInvalid,
)
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import (
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    RuntimeAuthorityReceipt,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    verify_selected_backend,
)
from oh_goon.integrations.fleet_plan_runtime_authority import expected_runtime_authority_sources

_RUNNER_PROTOCOL = 4


class OpenSandboxRuntimeKind(str):
    RUNC = "runc"
    GVISOR = "gvisor"
    KATA = "kata"
    FIRECRACKER = "firecracker"


@dataclass(frozen=True, slots=True)
class OpenSandboxSettings:
    domain: str
    api_key: str = field(repr=False)
    image: str
    cli_executable: str
    runtime_attestation_file: str
    runtime_attestation_public_key_file: str
    runtime: str
    protocol: str
    # The deployment's credential *selector* is identity, never the secret value.
    # Direct transport fixtures may omit it; production inventory must supply one.
    api_key_env: str | None = None

    def validate(self) -> None:
        if self.protocol not in {"http", "https"}:
            raise FleetPlanInvalid("OpenSandbox protocol must be http or https")
        if self.runtime not in {
            OpenSandboxRuntimeKind.KATA,
            OpenSandboxRuntimeKind.FIRECRACKER,
        }:
            raise FleetPlanInvalid(
                f"Unsupported OpenSandbox secure runtime: {self.runtime}; "
                "Oh-Goon requires Kata or Firecracker for this deny-all-egress backend. "
                "Plain runc is not a strong isolation boundary and OpenSandbox's current "
                "egress sidecar is incompatible with gVisor."
            )
        cli = Path(self.cli_executable)
        if not cli.is_absolute() or not cli.is_file() or not os.access(cli, os.X_OK):
            raise FleetPlanInvalid(
                "Fleet OpenSandbox executor requires an absolute executable osb CLI path"
            )
        if "@sha256:" not in self.image:
            raise FleetPlanInvalid(
                "Fleet OpenSandbox runner image must be digest-pinned with @sha256:; mutable tags are not executable authority"
            )
        _validate_runtime_attestation(self)

    @property
    def runtime_attestation_sha256(self) -> str:
        return _file_sha256(self.runtime_attestation_file)

    @property
    def runtime_attestation_public_key_sha256(self) -> str:
        return _file_sha256(self.runtime_attestation_public_key_file)


class OpenSandboxAttemptTransport(Protocol):
    def require_available(self, settings: OpenSandboxSettings) -> None: ...

    def execute(
        self,
        settings: OpenSandboxSettings,
        request: IsolatedProcessRequest,
        *,
        expected_runtime_authority: RuntimeAuthorityReceipt,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
    ) -> CommandResult: ...


class OpenSandboxProcessIsolation:
    """Experimental one-sandbox-per-attempt backend behind Fleet's isolation contract."""

    def __init__(
        self,
        settings: OpenSandboxSettings,
        *,
        transport: OpenSandboxAttemptTransport | None = None,
    ) -> None:
        settings.validate()
        if transport is None:
            from oh_goon.integrations._fleet_plan_opensandbox_transport import (
                OpenSandboxCliTransport,
            )

            transport = OpenSandboxCliTransport()
        self.settings = settings
        self._transport = transport
        self.capabilities = _capabilities_for(settings.runtime)

    def configuration_identity(self) -> str:
        return stable_id(
            "process-isolation-backend",
            {
                "backend": self.capabilities.backend_id,
                "domain": self.settings.domain,
                "protocol": self.settings.protocol,
                # Credential identity is a semantic handle; never hash material.
                "api_key_env": self.settings.api_key_env,
                "image": self.settings.image,
                "runtime": self.settings.runtime,
                "runtime_attestation_sha256": self.settings.runtime_attestation_sha256,
                "runtime_attestation_public_key_sha256": self.settings.runtime_attestation_public_key_sha256,
                "runner_protocol": _RUNNER_PROTOCOL,
                "osb_sha256": _executable_sha256(self.settings.cli_executable),
            },
        )

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        if any(step.capabilities.egress.mode != "none" for step in plan.steps):
            raise FleetPlanInvalid(
                "Experimental Fleet OpenSandbox execution currently supports egress.mode=none only"
            )
        if any(step.capabilities.nested_strict_execution for step in plan.steps):
            raise FleetPlanInvalid(
                "Experimental Fleet OpenSandbox execution does not provide nested strict execution delegation"
            )
        self._transport.require_available(self.settings)

    def execute(
        self,
        request: IsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested: Callable[[], bool] | None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult:
        del process_observer
        if runtime_supervision is not None:
            raise FleetPlanInvalid(
                "OpenSandbox runtime supervision requires remote canonical signal transport"
            )
        verify_selected_backend(self, request)
        try:
            self._transport.require_available(self.settings)
        except FleetPlanInvalid as exc:
            raise ExecutorPlacementLost(
                "The admitted OpenSandbox executor became unavailable after RUN_ADMITTED"
            ) from exc
        expected_sources = expected_runtime_authority_sources(request)
        expected_runtime_authority = expected_sources.receipt
        effective_cancel_requested = _combined_cancel_requested(
            cancel_requested,
            request.termination_requested,
        )
        if stream_output is None:
            result = self._transport.execute(
                self.settings,
                request,
                expected_runtime_authority=expected_runtime_authority,
                timeout=timeout,
                cancel_requested=effective_cancel_requested,
            )
        else:
            result = self._transport.execute(
                self.settings,
                request,
                expected_runtime_authority=expected_runtime_authority,
                timeout=timeout,
                cancel_requested=effective_cancel_requested,
                stream_output=stream_output,
            )
        if result.runtime_authority != expected_runtime_authority:
            raise ExecutionAuthorityDivergence(
                "OpenSandbox runtime authority observation did not match admitted/materialized "
                "workload authority"
            )
        economics = expected_sources.economics
        if economics is None:
            return result
        return replace(
            result,
            execution_authority_economics=(
                replace(
                    economics,
                    terminal_proof_bytes=economics.reused_bytes + economics.recomputed_bytes,
                ),
            ),
        )


def _combined_cancel_requested(
    cancel_requested: Callable[[], bool] | None,
    termination_requested: Callable[[], TerminationSignal | None] | None,
) -> Callable[[], bool] | None:
    if cancel_requested is None and termination_requested is None:
        return None

    def combined() -> bool:
        if termination_requested is not None and termination_requested() is not None:
            return True
        return cancel_requested is not None and cancel_requested()

    return combined


def _capabilities_for(runtime: str) -> IsolationBackendCapabilities:
    if runtime not in {OpenSandboxRuntimeKind.KATA, OpenSandboxRuntimeKind.FIRECRACKER}:
        raise FleetPlanInvalid(f"Unsupported OpenSandbox runtime: {runtime}")
    return IsolationBackendCapabilities(
        backend_id=f"opensandbox-{runtime}",
        private_filesystem=True,
        read_only_workspace=False,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=False,
        kernel_boundary=IsolationKernelBoundary.VIRTUAL_MACHINE,
        remote_execution=True,
        experimental=True,
    )


def _file_sha256(path: str) -> str:
    candidate = Path(path)
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _executable_sha256(path: str) -> str:
    return _file_sha256(path)


def _validate_runtime_attestation(settings: OpenSandboxSettings) -> None:
    attestation_path = Path(settings.runtime_attestation_file)
    key_path = Path(settings.runtime_attestation_public_key_file)
    if not attestation_path.is_absolute() or not attestation_path.is_file():
        raise FleetPlanInvalid(
            "Fleet OpenSandbox executor requires an absolute runtime_attestation_file"
        )
    if not key_path.is_absolute() or not key_path.is_file():
        raise FleetPlanInvalid(
            "Fleet OpenSandbox executor requires an absolute runtime_attestation_public_key_file"
        )
    try:
        document = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetPlanInvalid("OpenSandbox runtime attestation is not valid JSON") from exc
    if not isinstance(document, dict) or set(document) != {"claims", "signature"}:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation must contain only signed claims and signature"
        )
    claims = document.get("claims")
    signature_text = document.get("signature")
    if not isinstance(claims, dict) or not isinstance(signature_text, str):
        raise FleetPlanInvalid("OpenSandbox runtime attestation has invalid signed fields")
    required = {
        "schema",
        "domain",
        "protocol",
        "secure_runtime",
        "server_configuration_sha256",
        "issued_at",
        "expires_at",
    }
    if set(claims) != required:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation claims are incomplete or contain unknown authority fields"
        )
    expected = {
        "schema": "oh-goon.opensandbox-runtime-attestation.v1",
        "domain": settings.domain,
        "protocol": settings.protocol,
        "secure_runtime": settings.runtime,
    }
    if any(claims.get(k) != v for k, v in expected.items()):
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation does not bind the configured endpoint/runtime"
        )
    server_sha = claims.get("server_configuration_sha256")
    if not isinstance(server_sha, str) or re.fullmatch(r"[0-9a-f]{64}", server_sha) is None:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation requires server_configuration_sha256"
        )
    issued = _attestation_time(claims.get("issued_at"), field="issued_at")
    expires = _attestation_time(claims.get("expires_at"), field="expires_at")
    now = datetime.now(UTC)
    if issued > now:
        raise FleetPlanInvalid("OpenSandbox runtime attestation is not yet valid")
    if expires <= now or expires <= issued:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation is expired or has an invalid validity window"
        )
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except Exception as exc:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation signature is not valid base64"
        ) from exc
    try:
        key = serialization.load_pem_public_key(key_path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise FleetPlanInvalid("OpenSandbox runtime attestation public key is invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise FleetPlanInvalid("OpenSandbox runtime attestation public key must be Ed25519")
    try:
        key.verify(signature, canonical)
    except InvalidSignature as exc:
        raise FleetPlanInvalid(
            "OpenSandbox runtime attestation signature verification failed"
        ) from exc


def _attestation_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise FleetPlanInvalid(f"OpenSandbox runtime attestation {field} must be RFC3339 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FleetPlanInvalid(
            f"OpenSandbox runtime attestation {field} is not valid RFC3339"
        ) from exc
    if parsed.tzinfo is None:
        raise FleetPlanInvalid(f"OpenSandbox runtime attestation {field} must include timezone")
    return parsed.astimezone(UTC)
