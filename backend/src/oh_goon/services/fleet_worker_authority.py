"""Protected transport of one Run's existing deployment enrollment.

The original admission and native issuer owners retain all authority. This
codec neither enrolls keys nor re-resolves policy, inventory, source or a Plan.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import ConfigDict, Field, model_validator

from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS
from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    ProtectedReceiptKey,
    ReceiptAuthenticator,
    TrustProtocolError,
)
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import FleetRepositoryScope, FleetRunAdmissionIntent
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.native_issuer.enforcer_lace_execution_environment_issuer import (
    InventorySnapshotIdentity,
    _inventory_snapshot,
)
from oh_goon.runtime._executor_configuration import ExecutorPoolConfiguration
from oh_goon.runtime.enforcer_lace_issuer_keyring import IssuerKeyReference
from oh_goon.services.fleet_native_run_contract_compiler import DeploymentRunContractPolicy
from oh_goon.services.fleet_policy_enrollment import FleetPolicyEnrollment
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_strict_policy_enrollment import (
    StrictFleetEnrollmentIntent,
    StrictFleetPolicyEnrollment,
)


class FleetWorkerAuthority(ImmutableModel):
    """Privileged, bounded worker-channel input; never public Run evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    run_id: str = Field(min_length=1, max_length=160)
    kind: Literal["composition", "policy", "strict"]
    protected_key: ProtectedReceiptKey = Field(repr=False)
    contract: AdmittedRunContract | None = Field(default=None, repr=False)
    deployment_policy: DeploymentRunContractPolicy | None = Field(default=None, repr=False)
    configured: ResolvedExecutionEnvironment = Field(repr=False)
    current: ResolvedExecutionEnvironment = Field(repr=False)
    intent: FleetRunAdmissionIntent | None = Field(default=None, repr=False)
    strict_intent: StrictFleetEnrollmentIntent | None = Field(default=None, repr=False)
    repository_scope: FleetRepositoryScope = Field(repr=False)
    targets: tuple[RepositoryIdentity, ...] = Field(min_length=2, repr=False)
    path_authority: StrictHostPathAuthority = Field(repr=False)
    key_directory: Path = Field(repr=False)
    trusted_public_roots_hex: dict[str, tuple[str, str]] = Field(repr=False)
    control_state_root: Path | None = Field(default=None, repr=False)
    executor_inventory_file: Path = Field(repr=False)
    executor_inventory_identity: InventorySnapshotIdentity = Field(repr=False)
    trusted_key_directory: Path = Field(repr=False)
    issuer_references: dict[str, IssuerKeyReference] = Field(repr=False)
    public_root_owner_uid: int | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def require_exact_enrollment(self) -> FleetWorkerAuthority:
        if self.kind == "composition":
            if self.contract is None or self.intent is None or self.strict_intent is not None:
                raise ValueError("complete original Fleet composition required")
            self.contract.verify()
            if self.contract.fields["run_id"] != self.run_id:
                raise ValueError("worker enrollment belongs to another Run")
        elif (
            self.contract is not None
            or self.deployment_policy is None
            or (self.kind == "policy" and (self.intent is None or self.strict_intent is not None))
            or (self.kind == "strict" and (self.strict_intent is None or self.intent is not None))
        ):
            raise ValueError("complete original Fleet policy enrollment required")
        if self.deployment_policy is not None:
            self.deployment_policy.read()
        if (
            set(self.trusted_public_roots_hex) != AUTHORITY_FIELDS
            or set(self.issuer_references) != AUTHORITY_FIELDS
        ):
            raise ValueError("six original Fleet issuer roots required")
        try:
            for _, value in self.trusted_public_roots_hex.values():
                key = bytes.fromhex(value)
                if len(key) != 32 or key.hex() != value:
                    raise ValueError
        except ValueError:
            raise ValueError("invalid original Fleet public-root encoding") from None
        for path in (self.key_directory, self.executor_inventory_file, self.trusted_key_directory):
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("captured absolute Fleet deployment paths required")
        return self


def capture_fleet_worker_authority(
    provisioner: FleetPreworkProvisioner | None,
    run_id: str,
    configuration: ExecutorPoolConfiguration | None,
    *,
    expected_inventory_identity: tuple[object, ...] | None = None,
) -> FleetWorkerAuthority | None:
    """Freeze exactly the selected enrollment before any worker is launched."""
    if provisioner is None:
        return None
    if type(provisioner) is not FleetPreworkProvisioner or configuration is None:
        raise TrustProtocolError("original Fleet provisioner and resolved inventory required")
    provisioner.require_enrolled_identity(run_id)
    expected = expected_inventory_identity or provisioner.executor_inventory_identity
    before = _inventory_snapshot(provisioner.executor_inventory_file)[1]
    if expected is None or before != expected:
        raise TrustProtocolError("Fleet executor inventory differs from captured configuration")
    captured = replace(
        provisioner,
        executor_configuration=configuration,
        executor_inventory_identity=before,
    )
    binding = captured.resolve(run_id)
    if type(binding.authenticator) is not ReceiptAuthenticator:
        raise TrustProtocolError("original Fleet receipt authenticator required")
    kind: Literal["composition", "policy", "strict"] = (
        "composition"
        if type(binding) is FleetPreworkComposition
        else "strict"
        if type(binding) is StrictFleetPolicyEnrollment
        else "policy"
    )
    # Private project archive caches are deliberately absent. The admitted Run
    # executor restores original ProjectDelivery references from its content store;
    # worker execution never re-enters enrollment/admission intent matching.
    return FleetWorkerAuthority(
        run_id=run_id,
        kind=kind,
        protected_key=binding.authenticator.capture_worker_key(),
        contract=binding.contract if isinstance(binding, FleetPreworkComposition) else None,
        deployment_policy=binding.deployment_policy,
        configured=binding.configured,
        current=binding.current,
        intent=binding.intent if isinstance(binding.intent, FleetRunAdmissionIntent) else None,
        strict_intent=(
            binding.intent if isinstance(binding.intent, StrictFleetEnrollmentIntent) else None
        ),
        repository_scope=binding.repository_scope,
        targets=binding.targets,
        path_authority=binding.path_authority,
        key_directory=binding.key_directory,
        trusted_public_roots_hex={
            name: (issuer_id, key.hex())
            for name, (issuer_id, key) in binding.trusted_public_roots.items()
        },
        control_state_root=binding.control_state_root,
        executor_inventory_file=captured.executor_inventory_file,
        executor_inventory_identity=before,
        trusted_key_directory=captured.trusted_key_directory,
        issuer_references=dict(captured.issuer_references),
        public_root_owner_uid=captured.public_root_owner_uid,
    )


class _SharedEnrollment(TypedDict):
    authenticator: ReceiptAuthenticator
    configured: ResolvedExecutionEnvironment
    current: ResolvedExecutionEnvironment
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    path_authority: StrictHostPathAuthority
    key_directory: Path
    trusted_public_roots: dict[str, tuple[str, bytes]]
    control_state_root: Path | None


def restore_fleet_worker_authority(
    payload: FleetWorkerAuthority,
    configuration: ExecutorPoolConfiguration | None,
) -> FleetPreworkProvisioner:
    """Restore the same Run enrollment and reprove its native deployment owners."""
    if type(payload) is not FleetWorkerAuthority or configuration is None:
        raise TrustProtocolError("captured Fleet worker authority and inventory required")
    common = _SharedEnrollment(
        authenticator=ReceiptAuthenticator.restore_worker_key(payload.protected_key),
        configured=payload.configured,
        current=payload.current,
        repository_scope=payload.repository_scope,
        targets=payload.targets,
        path_authority=payload.path_authority,
        key_directory=payload.key_directory,
        trusted_public_roots={
            name: (issuer_id, bytes.fromhex(value))
            for name, (issuer_id, value) in payload.trusted_public_roots_hex.items()
        },
        control_state_root=payload.control_state_root,
    )
    binding: FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment
    if payload.kind == "composition":
        assert payload.contract is not None and payload.intent is not None
        binding = FleetPreworkComposition(
            contract=payload.contract,
            intent=payload.intent,
            deployment_policy=payload.deployment_policy,
            **common,
        )
    elif payload.kind == "strict":
        assert payload.strict_intent is not None and payload.deployment_policy is not None
        binding = StrictFleetPolicyEnrollment(
            run_id=payload.run_id,
            intent=payload.strict_intent,
            deployment_policy=payload.deployment_policy,
            **common,
        )
    else:
        assert payload.intent is not None and payload.deployment_policy is not None
        binding = FleetPolicyEnrollment(
            run_id=payload.run_id,
            intent=payload.intent,
            deployment_policy=payload.deployment_policy,
            **common,
        )
    restored = FleetPreworkProvisioner(
        enrolled=(binding,),
        executor_inventory_file=payload.executor_inventory_file,
        trusted_key_directory=payload.trusted_key_directory,
        issuer_references=payload.issuer_references,
        control_state_root=payload.control_state_root,
        public_root_owner_uid=payload.public_root_owner_uid,
        executor_configuration=configuration,
        executor_inventory_identity=payload.executor_inventory_identity,
    )
    restored.resolve(payload.run_id)
    return restored
