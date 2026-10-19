"""Explicit, deployment-provisioned Fleet Run prework authority (M170).

This is an input *binding*, not another resolver, signer, admission writer or
source of inferred credentials. One binding is immutable and scoped to one Run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    ReceiptAuthenticator,
    TrustProtocolError,
)
from oh_goon.core.execution_environment import ResolvedExecutionEnvironment
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import FleetRepositoryScope, FleetRunAdmissionIntent
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.services.fleet_native_run_contract_compiler import DeploymentRunContractPolicy


@dataclass(frozen=True, slots=True)
class FleetPreworkComposition:
    """One explicitly entrusted Run, not a callable factory or ambient fallback."""

    contract: AdmittedRunContract
    authenticator: ReceiptAuthenticator
    configured: ResolvedExecutionEnvironment
    current: ResolvedExecutionEnvironment
    intent: FleetRunAdmissionIntent
    repository_scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    path_authority: StrictHostPathAuthority
    key_directory: Path
    trusted_public_roots: Mapping[str, tuple[str, bytes]]
    control_state_root: Path | None = None
    deployment_policy: DeploymentRunContractPolicy | None = None

    def require_matching(
        self,
        *,
        run_id: str,
        intent: FleetRunAdmissionIntent,
        scope: FleetRepositoryScope,
        targets: list[RepositoryIdentity],
    ) -> None:
        if (
            type(self) is not FleetPreworkComposition
            or type(self.contract) is not AdmittedRunContract
            or type(self.authenticator) is not ReceiptAuthenticator
            or type(self.configured) is not ResolvedExecutionEnvironment
            or type(self.current) is not ResolvedExecutionEnvironment
            or type(self.path_authority) is not StrictHostPathAuthority
            or not isinstance(self.key_directory, Path)
            or not self.key_directory.is_absolute()
            or len(self.targets) < 2
            or self.contract.fields["run_id"] != run_id
            or self.intent != intent
            or self.repository_scope != scope
            or self.targets != tuple(targets)
        ):
            raise TrustProtocolError(
                "deployment Fleet prework identity does not match original Plan and Run"
            )
        # Neither environment equality nor signer trust comes from the caller:
        # M169 revalidates both with their original native owners inside SQLite.
        self.contract.verify()
        self.configured.require_unchanged(self.current)
        if self.deployment_policy is not None:
            if type(self.deployment_policy) is not DeploymentRunContractPolicy:
                raise TrustProtocolError("original frozen deployment contract policy required")
            self.deployment_policy.read()
