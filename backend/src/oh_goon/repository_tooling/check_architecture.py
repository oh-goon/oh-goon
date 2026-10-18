from __future__ import annotations

import sys
from collections.abc import Callable

from oh_goon.repository_tooling.architecture.backend_boundary_contract import check_backend_boundaries
from oh_goon.repository_tooling.architecture.common import Violation
from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import check_co_r01_lifecycle_edges
from oh_goon.repository_tooling.architecture.external_development_tooling_contract import check_external_development_tooling_contract
from oh_goon.repository_tooling.architecture.diagnostic_ownership_contract import (
    check_diagnostic_ownership_contract,
)
from oh_goon.repository_tooling.architecture.goon_static_admission_contract import check_goon_static_admission_contract
from oh_goon.repository_tooling.architecture.hashmarks_harness_boundary_contract import (
    check_hashmarks_harness_boundary_contract,
)
from oh_goon.repository_tooling.architecture._hashmarks_verification_boundary_contract import (
    check_hashmarks_verification_boundary_contract,
)
from oh_goon.repository_tooling.architecture.shard_execution_contract import (
    check_shard_execution_contract,
)
from oh_goon.repository_tooling.architecture.filesystem_identity_contract import check_filesystem_identity_contract
from oh_goon.repository_tooling.architecture.frontend_base_contract import (
    check_frontend_e2e_fixture_type_integrity,
    check_frontend_local_only,
)
from oh_goon.repository_tooling.architecture.simple_template_contract import check_simple_template_contract
from oh_goon.repository_tooling.architecture.source_layout_contract import check_source_layout_contract
from oh_goon.repository_tooling.architecture.structural_authority_contract import check_structural_authority_contract
from oh_goon.repository_tooling.architecture.release_boundary_contract import (
    check_release_boundary_contract,
)
from oh_goon.repository_tooling.architecture.repository_understanding_qualification_boundary_contract import (
    check_repository_understanding_qualification_boundary_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import check_x0_single_execution_path
from oh_goon.repository_tooling.architecture.qualification_environment_contract import check_qualification_environment_contract
from oh_goon.repository_tooling.architecture.trust_authority_registry_contract import check_trust_authority_registry
from oh_goon.repository_tooling.architecture.offline_custody_review_contract import check_offline_custody_review_contract
from oh_goon.repository_tooling.architecture.scout_observer_contract import check_scout_observer_contract
from oh_goon.repository_tooling.architecture.sandbox_provider_boundary_contract import (
    check_sandbox_provider_boundary_contract,
)
from oh_goon.repository_tooling.architecture.observability_projection_contract import check_observability_projection_contract
from oh_goon.repository_tooling.architecture.template_layout_contract import check_template_layout_contract

ArchitectureCheck = Callable[[], list[Violation]]
CHECKS: tuple[ArchitectureCheck, ...] = (
    check_backend_boundaries,
    check_goon_static_admission_contract,
    check_structural_authority_contract,
    check_filesystem_identity_contract,
    check_external_development_tooling_contract,
    check_hashmarks_harness_boundary_contract,
    check_hashmarks_verification_boundary_contract,
    check_repository_understanding_qualification_boundary_contract,
    check_sandbox_provider_boundary_contract,
    check_shard_execution_contract,
    check_source_layout_contract,
    check_frontend_e2e_fixture_type_integrity,
    check_frontend_local_only,
    check_template_layout_contract,
    check_simple_template_contract,
    check_scout_observer_contract,
    check_observability_projection_contract,
    check_diagnostic_ownership_contract,
    check_release_boundary_contract,
    check_x0_single_execution_path,
    check_co_r01_lifecycle_edges,
    check_qualification_environment_contract,
    check_trust_authority_registry,
    check_offline_custody_review_contract,
)


def main() -> int:
    failures = [
        (check.__name__, violation) for check in CHECKS for violation in check()
    ]
    if not failures:
        print("Architecture checks passed")
        return 0
    print("Architecture checks failed:", file=sys.stderr)
    for check_name, violation in failures:
        path = violation.path.relative_to(ROOT)
        print(f"  {check_name}", file=sys.stderr)
        print(f"    {path}: {violation.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
