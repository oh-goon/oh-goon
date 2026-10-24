"""M161: non-live environment identity from the *configured* executor owner.

The runtime's one executor-inventory loader and durable strict placement are
authoritative. This module neither chooses a fallback nor attests physical
backend containment/capabilities. The private signing key stays outside Enforcer.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    ISSUER_PROTOCOL,
    SIGNATURE_DOMAIN,
    IssuerEvidence,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    canonical_bytes,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.native_issuer.enforcer_lace_captured_run_issuer import _read_original_admission
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.journal import Journal

ENV_FIELD = "execution_environment_identity"


def _inventory_snapshot() -> tuple[Path, tuple[object, ...]]:
    """Observe exact deployment-selected inventory, rejecting file alias/replacement."""
    raw = os.environ.get(EXECUTORS_FILE_ENV)
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise TrustProtocolError("deployment-selected executor inventory required")
    path = Path(raw)
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or path.is_symlink()
            or path.resolve() != path
        ):
            raise TrustProtocolError("executor inventory must be unique regular canonical file")
        with path.open("rb") as stream:
            payload = stream.read()
            current = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise TrustProtocolError("executor inventory is unavailable") from exc

    def identity(item: os.stat_result) -> tuple[int, ...]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    if identity(info) != identity(current) or identity(info) != identity(after):
        raise TrustProtocolError("executor inventory changed during native observation")
    return path, (*identity(info), hashlib.sha256(payload).hexdigest())


def _observe(
    journal: Journal,
    run_id: str,
    *,
    control_state_root: Path | None,
    prework: PreworkRunCapture | None = None,
) -> tuple[RunAdmittedEvent, int, Path, tuple[object, ...], ResolvedExecutionEnvironment]:
    admitted, revision = (
        _read_original_admission(journal, run_id)
        if prework is None
        else _read_original_admission(journal, run_id, prework=prework)
    )
    placement = admitted.execution
    if not isinstance(placement, StrictFleetExecutionAdmission):
        raise TrustProtocolError("strict durable executor placement is required")
    before_path, before_file = _inventory_snapshot()
    try:
        inventory = configured_executor_pool(control_state_root=control_state_root)
        if inventory is None:
            raise TrustProtocolError("no deployment executor configuration")
        selected = tuple(
            executor
            for executor in inventory.executors
            if executor.identity.executor_id == placement.placement.executor_capacity.executor_id
        )
        if len(selected) != 1:
            raise TrustProtocolError("admitted executor is absent from deployment inventory")
        executor = selected[0]
        capacity = placement.placement.executor_capacity
        if (
            executor.identity.generation != capacity.executor_generation
            or isolation_backend_evidence(executor.backend) != placement.placement.isolation_backend
            or executor.capacity.cpu_millis != capacity.capacity_cpu_millis
            or executor.capacity.memory_mib * 1024 * 1024 != capacity.capacity_memory_bytes
            or executor.capacity.max_processes != capacity.capacity_max_processes
        ):
            raise TrustProtocolError("durable executor placement differs from deployment authority")
        environment = executor.resolve_environment(inventory.path_authority)
    except TrustProtocolError:
        raise
    except (FleetPlanInvalid, OSError, ValueError, TypeError) as exc:
        raise TrustProtocolError("configured executor environment owner is unavailable") from exc
    after_path, after_file = _inventory_snapshot()
    if before_path != after_path or before_file != after_file:
        raise TrustProtocolError("executor inventory changed while resolving environment")
    return admitted, revision, before_path, before_file, environment


def observed_admitted_run_execution_environment_identity(
    *,
    journal: Journal,
    run_id: str,
    control_state_root: Path | None = None,
) -> str:
    """Non-live observation, not a physical binding or capability attestation."""
    before = _observe(journal, run_id, control_state_root=control_state_root)
    after = _observe(journal, run_id, control_state_root=control_state_root)
    if before != after:
        raise TrustProtocolError("Run or Execution Environment changed during observation")
    return before[4].execution_environment_identity


def issue_observed_captured_run_execution_environment_identity(
    *,
    journal: Journal,
    contract: AdmittedRunContract,
    issuer_id: str,
    signing_key: Ed25519PrivateKey,
    control_state_root: Path | None = None,
    prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Sign only the immutable deployment/placement identity on two observations."""
    contract.verify()
    if (
        not isinstance(signing_key, Ed25519PrivateKey)
        or not isinstance(issuer_id, str)
        or not issuer_id
        or not issuer_id.isascii()
        or any(c in issuer_id for c in "\x00\n/")
    ):
        raise TrustProtocolError("independent Execution Environment signing authority required")
    fields = contract.fields
    before = _observe(
        journal, fields["run_id"], control_state_root=control_state_root, prework=prework
    )
    environment = before[4]
    if (
        environment.execution_environment_identity != fields[ENV_FIELD]
        or environment.executor_generation != fields["executor_generation"]
    ):
        raise TrustProtocolError("contract Execution Environment differs from admitted executor")
    payload = canonical_bytes(
        {
            "protocol": ISSUER_PROTOCOL,
            "field": ENV_FIELD,
            "run_id": fields["run_id"],
            "contract_identity": contract.identity,
            "identity": environment.execution_environment_identity,
            "executor_generation": fields["executor_generation"],
        }
    )
    after = _observe(
        journal, fields["run_id"], control_state_root=control_state_root, prework=prework
    )
    if before != after:
        raise TrustProtocolError("Run or Execution Environment changed during signing")
    message = SIGNATURE_DOMAIN + ENV_FIELD.encode("ascii") + b"\x00" + payload
    return IssuerEvidence(ENV_FIELD, issuer_id, payload, signing_key.sign(message).hex())
