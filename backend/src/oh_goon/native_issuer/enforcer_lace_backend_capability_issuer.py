"""M162: non-live issuer for the *backend-owned capability statement*.

A capability manifest identifies what the selected implementation declares. It is
NOT proof that Bubblewrap/cgroups or remote OpenSandbox isolation was physically
established. That evidence belongs to the launch/containment authority in Group B.
No caller-supplied capability mapping, fallback backend, or second selector exists.
"""
from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    ISSUER_PROTOCOL, SIGNATURE_DOMAIN, IssuerEvidence,
)
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract, TrustProtocolError, canonical_bytes, digest,
)
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import IsolationBackendCapabilities
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.native_issuer import enforcer_lace_execution_environment_issuer as environment_owner
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.journal import Journal
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture

CAPABILITY_FIELD = "backend_capability_manifest_identity"
CAPABILITY_DOMAIN = "oh-goon/backend-capability-manifest/v1"


def _observe(journal: Journal, run_id: str, *, control_state_root: Path | None,
             prework: PreworkRunCapture | None = None):
    """Use the M161 native inventory/placement owner; observe manifest, not policy input."""
    observed = environment_owner._observe(
        journal, run_id, control_state_root=control_state_root, prework=prework,
    )
    admitted, revision, path, file_identity, environment = observed
    try:
        inventory = configured_executor_pool(control_state_root=control_state_root)
        if inventory is None:
            raise TrustProtocolError("backend capability inventory unavailable")
        selected = tuple(executor for executor in inventory.executors
                         if executor.identity.executor_id == environment.executor_id
                         and executor.identity.generation == environment.executor_generation)
        if len(selected) != 1:
            raise TrustProtocolError("admitted backend capability owner is absent")
        backend = selected[0].backend
        statement = backend.capabilities
        if not isinstance(statement, IsolationBackendCapabilities):
            raise TrustProtocolError("backend capability statement is not native")
        if (isolation_backend_evidence(backend) != admitted.execution.placement.isolation_backend
                or backend.configuration_identity() != environment.backend_configuration_identity):
            raise TrustProtocolError("backend capability owner differs from durable placement")
        native_identity = digest(CAPABILITY_DOMAIN, canonical_bytes(statement.model_dump(mode="json")))
        if (statement.backend_id != environment.backend
                or native_identity != environment.capability_manifest_identity):
            raise TrustProtocolError("backend capability manifest changed since environment resolution")
    except TrustProtocolError:
        raise
    except (FleetPlanInvalid, OSError, TypeError, ValueError, AttributeError) as exc:
        raise TrustProtocolError("native backend capability owner unavailable") from exc
    after_path, after_identity = environment_owner._inventory_snapshot()
    if (after_path, after_identity) != (path, file_identity):
        raise TrustProtocolError("executor inventory changed while observing backend capabilities")
    return admitted, revision, path, file_identity, environment, native_identity


def observed_admitted_run_backend_capability_manifest_identity(
    *, journal: Journal, run_id: str, control_state_root: Path | None = None,
) -> str:
    """Reobserve the actual configured backend, without claiming physical enforcement."""
    before = _observe(journal, run_id, control_state_root=control_state_root)
    after = _observe(journal, run_id, control_state_root=control_state_root)
    if before != after:
        raise TrustProtocolError("Run or native backend capability manifest changed during observation")
    return before[5]


def issue_observed_captured_run_backend_capability_manifest_identity(
    *, journal: Journal, contract: AdmittedRunContract,
    issuer_id: str, signing_key: Ed25519PrivateKey,
    control_state_root: Path | None = None, prework: PreworkRunCapture | None = None,
) -> IssuerEvidence:
    """Sign only the native manifest identity matching the durable placement and Run."""
    contract.verify()
    if (not isinstance(signing_key, Ed25519PrivateKey)
            or not isinstance(issuer_id, str) or not issuer_id or not issuer_id.isascii()
            or any(c in issuer_id for c in "\x00\n/")):
        raise TrustProtocolError("independent backend capability signing authority required")
    fields = contract.fields
    before = _observe(journal, fields["run_id"], control_state_root=control_state_root, prework=prework)
    environment = before[4]
    if (before[5] != fields[CAPABILITY_FIELD]
            or environment.executor_generation != fields["executor_generation"]
            or environment.execution_environment_identity != fields["execution_environment_identity"]):
        raise TrustProtocolError("contract capability manifest differs from native backend authority")
    payload = canonical_bytes({
        "protocol": ISSUER_PROTOCOL, "field": CAPABILITY_FIELD,
        "run_id": fields["run_id"], "contract_identity": contract.identity,
        "identity": before[5], "executor_generation": fields["executor_generation"],
    })
    after = _observe(journal, fields["run_id"], control_state_root=control_state_root, prework=prework)
    if before != after:
        raise TrustProtocolError("Run or native backend capability manifest changed during signing")
    return IssuerEvidence(
        CAPABILITY_FIELD, issuer_id, payload,
        signing_key.sign(SIGNATURE_DOMAIN + CAPABILITY_FIELD.encode("ascii") + b"\x00" + payload).hex(),
    )
