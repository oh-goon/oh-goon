from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_authority_certification import (
    _check_certification_preexecution_authority,
    _check_execution_authority_ii,
    _check_scheduler_backpressure_authority,
    _check_self_verification_accelerator,
)
from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_backend_dependency_complete_partition,
    _check_certification_storage_loss_boundary,
    _check_development_test_runner_boundary,
    _check_local_executor_control_state,
)
from oh_goon.repository_tooling.architecture._structural_authority_freshness import (
    _check_multitab_schedule_uniqueness,
)
from oh_goon.repository_tooling.architecture._structural_authority_frontend import (
    _check_browser_persistence_boundary,
    _check_frontend_transport_ownership,
    _check_mutable_resource_fences,
)
from oh_goon.repository_tooling.architecture._structural_authority_workflow import (
    check_make_single_path,
    check_no_persisted_success_mirror,
    check_workflow_runner_boundary,
)
from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.structural_launch_contract import (
    _check_launch_manifest_foundation,
    _check_runtime_authority_registry,
)
from oh_goon.repository_tooling.architecture.structural_project_python_contract import (
    _check_project_python_execution_ownership,
)


def check_structural_authority_contract(root: Path = ROOT) -> list[Violation]:
    """Keep simplicity/ownership invariants executable without becoming runtime authority.

    The checker derives facts from the real Make/Python/TypeScript owners. It has
    no configuration registry and is never consulted by application execution.
    """
    return [
        *check_make_single_path(root),
        *_check_development_test_runner_boundary(root),
        *_check_backend_dependency_complete_partition(root),
        *check_workflow_runner_boundary(root),
        *check_no_persisted_success_mirror(root),
        *_check_frontend_transport_ownership(root),
        *_check_browser_persistence_boundary(root),
        *_check_mutable_resource_fences(root),
        *_check_multitab_schedule_uniqueness(root),
        *_check_project_python_execution_ownership(root),
        *_check_local_executor_control_state(root),
        *_check_certification_storage_loss_boundary(root),
        *_check_self_verification_accelerator(root),
        *_check_certification_preexecution_authority(root),
        *_check_launch_manifest_foundation(root),
        *_check_runtime_authority_registry(root),
        *_check_execution_authority_ii(root),
        *_check_scheduler_backpressure_authority(root),
    ]
