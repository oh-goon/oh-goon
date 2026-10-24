"""M174: explicitly enrolled policy-only Fleet authority; no derived Run digests.

The deployment authorizes the Run before planning. The canonical Journal and
native issuer owners compile the measured Run inside the original reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetEnrollmentIntent

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator, TrustProtocolError
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import FleetRepositoryScope, FleetRunAdmissionIntent
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.services.fleet_native_run_contract_compiler import DeploymentRunContractPolicy


@dataclass(frozen=True, slots=True)
class FleetPolicyEnrollment:
    """Complete pre-planning authorization; deliberately has no contract field."""

    run_id: str
    deployment_policy: DeploymentRunContractPolicy
    authenticator: ReceiptAuthenticator
    configured: ResolvedExecutionEnvironment
    current: ResolvedExecutionEnvironment
    intent: FleetRunAdmissionIntent | StrictFleetEnrollmentIntent
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    path_authority: StrictHostPathAuthority
    key_directory: Path
    trusted_public_roots: Mapping[str, tuple[str, bytes]]
    control_state_root: Path | None = None

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or not self.run_id
            or type(self.deployment_policy) is not DeploymentRunContractPolicy
            or type(self.authenticator) is not ReceiptAuthenticator
            or type(self.configured) is not ResolvedExecutionEnvironment
            or type(self.current) is not ResolvedExecutionEnvironment
            or type(self.path_authority) is not StrictHostPathAuthority
            or not isinstance(self.key_directory, Path)
            or not self.key_directory.is_absolute()
            or type(self.targets) is not tuple
            or len(self.targets) < 2
            or (
                self.control_state_root is not None
                and (
                    not isinstance(self.control_state_root, Path)
                    or not self.control_state_root.is_absolute()
                )
            )
        ):
            raise TrustProtocolError("complete policy-only Fleet deployment enrollment required")
        self.deployment_policy.read()
        self.configured.require_unchanged(self.current)
        if (
            not isinstance(self.trusted_public_roots, Mapping)
            or len(self.trusted_public_roots) != 6
            or any(
                type(field) is not str
                or type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not bytes
                for field, pair in self.trusted_public_roots.items()
            )
        ):
            raise TrustProtocolError("six independent Fleet issuer roots required")
        # Never retain a deployment caller's mutable public-root dictionary.
        object.__setattr__(
            self, "trusted_public_roots", MappingProxyType(dict(self.trusted_public_roots))
        )

    def require_matching(
        self,
        *,
        run_id: str,
        intent: FleetRunAdmissionIntent,
        scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> None:
        if (
            type(self) is not FleetPolicyEnrollment
            or self.run_id != run_id
            or self.intent != intent
            or self.repository_scope != scope
            or self.targets != tuple(targets)
        ):
            raise TrustProtocolError(
                "policy-only enrollment differs from original Fleet Plan and Run"
            )
        self.deployment_policy.read()
        self.configured.require_unchanged(self.current)
