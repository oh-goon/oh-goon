from __future__ import annotations

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class PlanExecutionAuthorityMaterializationEvidence(ImmutableModel):
    """Non-authoritative projection of the already-bound executable materialization."""

    receipt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    logical_sources: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    package_executables: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    owned_scripts: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    direct_workspace_executable: str | None = Field(default=None, max_length=240)


class PlanExecutionAuthorityRuntimeEvidence(ImmutableModel):
    """Diagnostic projection of the runtime receipt observed by the execution owner."""

    receipt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executable_path: str = Field(min_length=1, max_length=500)
    executable_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    path_authority_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    interpreter_path: str | None = Field(default=None, max_length=500)
    interpreter_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    execution_materialization_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class PlanExecutionAuthorityAtomicHandoffEvidence(ImmutableModel):
    """Derived terminal handoff proof; never an independent execution authority."""

    state: Literal["PROVEN", "NOT_OBSERVED"]
    mechanism: Literal["sealed_memfd_bind", "remote_sealed_execution", "not_observed"]
    terminal_proof_bytes: int = Field(default=0, ge=0)


class PlanExecutionAuthorityDiagnosticEvidence(ImmutableModel):
    """Bounded F8 projection composed from existing authority/economics receipts."""

    materialization: PlanExecutionAuthorityMaterializationEvidence
    runtime: PlanExecutionAuthorityRuntimeEvidence | None = None
    atomic_handoff: PlanExecutionAuthorityAtomicHandoffEvidence
