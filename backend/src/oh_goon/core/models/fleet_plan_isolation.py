from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class IsolationThreatModel(StrEnum):
    """Caller-declared trust boundary; never names an implementation backend."""

    TRUSTED_REPOSITORY = "trusted_repository"
    UNTRUSTED_REPOSITORY = "untrusted_repository"
    HOSTILE_GENERATED_CODE = "hostile_generated_code"


class IsolationKernelBoundary(StrEnum):
    """Minimum kernel boundary required from an execution backend."""

    SHARED_HOST_KERNEL = "shared_host_kernel"
    APPLICATION_KERNEL = "application_kernel"
    VIRTUAL_MACHINE = "virtual_machine"


_KERNEL_STRENGTH = {
    IsolationKernelBoundary.SHARED_HOST_KERNEL: 0,
    IsolationKernelBoundary.APPLICATION_KERNEL: 1,
    IsolationKernelBoundary.VIRTUAL_MACHINE: 2,
}


class CicdResourcePolicy(ImmutableModel):
    """Optional author-imposed process-tree resource caps.

    Omitted fields are deliberately *not* replaced by static product defaults. They
    inherit the capacity available from the selected executor at execution time, so
    an ordinary CI/CD plan can run on a laptop, workstation or large worker without
    silently acquiring a 2 CPU / 2 GiB / 64 PID contract. Executor-capacity evidence
    records the capacity observed for each admitted run; that capacity may change
    between runs.
    """

    cpu_millis: int | None = Field(default=None, ge=100, le=64000)
    memory_mib: int | None = Field(default=None, ge=64, le=262144)
    max_processes: int | None = Field(default=None, ge=16, le=4096)

    @property
    def memory_bytes(self) -> int | None:
        if self.memory_mib is None:
            return None
        return self.memory_mib * 1024 * 1024


class IsolationRequirements(ImmutableModel):
    """Backend-independent guarantees required before a strict CI/CD run may be admitted."""

    private_filesystem: bool = True
    private_process_tree: bool = True
    enforced_network_policy: bool = True
    content_identified_runtime: bool = True
    exact_argv: bool = True
    disposable_attempt: bool = True
    immutable_authority_after_start: bool = True
    enforced_cpu_limit: bool = False
    enforced_memory_limit: bool = False
    enforced_process_limit: bool = False
    minimum_kernel_boundary: IsolationKernelBoundary


class IsolationBackendCapabilities(ImmutableModel):
    """Immutable capability statement for one configured process-isolation backend."""

    backend_id: str
    private_filesystem: bool
    read_only_workspace: bool = False
    private_process_tree: bool
    enforced_network_policy: bool
    content_identified_runtime: bool
    exact_argv: bool
    disposable_attempt: bool
    immutable_authority_after_start: bool
    enforced_cpu_limit: bool
    enforced_memory_limit: bool
    enforced_process_limit: bool
    kernel_boundary: IsolationKernelBoundary
    remote_execution: bool = False
    experimental: bool = False

    def satisfies(self, requirements: IsolationRequirements) -> bool:
        return (
            (not requirements.private_filesystem or self.private_filesystem)
            and (not requirements.private_process_tree or self.private_process_tree)
            and (not requirements.enforced_network_policy or self.enforced_network_policy)
            and (not requirements.content_identified_runtime or self.content_identified_runtime)
            and (not requirements.exact_argv or self.exact_argv)
            and (not requirements.disposable_attempt or self.disposable_attempt)
            and (
                not requirements.immutable_authority_after_start
                or self.immutable_authority_after_start
            )
            and (not requirements.enforced_cpu_limit or self.enforced_cpu_limit)
            and (not requirements.enforced_memory_limit or self.enforced_memory_limit)
            and (not requirements.enforced_process_limit or self.enforced_process_limit)
            and _KERNEL_STRENGTH[self.kernel_boundary]
            >= _KERNEL_STRENGTH[requirements.minimum_kernel_boundary]
        )


def isolation_requirements(
    trust_repository_code: bool | IsolationThreatModel,
    resources: CicdResourcePolicy | None = None,
) -> IsolationRequirements:
    """Compile the developer-facing trust boolean to backend requirements."""
    # A threat-model StrEnum is a truthy string. Boolean coercion here used to
    # silently downgrade UNTRUSTED and HOSTILE callers to TRUSTED host isolation.
    # This is the original backend requirement compiler: resolve this choice
    # exactly once, before any capability comparison or placement work.
    if type(trust_repository_code) is bool:
        threat_model = (
            IsolationThreatModel.TRUSTED_REPOSITORY
            if trust_repository_code
            else IsolationThreatModel.UNTRUSTED_REPOSITORY
        )
    elif isinstance(trust_repository_code, IsolationThreatModel):
        threat_model = trust_repository_code
    else:
        raise ValueError("Fleet isolation requires a boolean trust policy or explicit threat model")
    if threat_model is IsolationThreatModel.TRUSTED_REPOSITORY:
        boundary = IsolationKernelBoundary.SHARED_HOST_KERNEL
    elif threat_model is IsolationThreatModel.UNTRUSTED_REPOSITORY:
        boundary = IsolationKernelBoundary.APPLICATION_KERNEL
    elif threat_model is IsolationThreatModel.HOSTILE_GENERATED_CODE:
        boundary = IsolationKernelBoundary.VIRTUAL_MACHINE
    else:  # pragma: no cover - exhaustive StrEnum guard
        raise ValueError(f"Unhandled Fleet isolation threat model: {threat_model}")
    return IsolationRequirements(
        minimum_kernel_boundary=boundary,
        enforced_cpu_limit=resources is not None and resources.cpu_millis is not None,
        enforced_memory_limit=resources is not None and resources.memory_mib is not None,
        enforced_process_limit=resources is not None and resources.max_processes is not None,
    )
