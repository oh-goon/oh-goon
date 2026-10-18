from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    check_project_dependency_contract,
)
from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)
from oh_goon.repository_tooling.architecture._structural_project_tooling_contract import (
    check_project_tooling_contract,
)
from oh_goon.repository_tooling.architecture.common import Violation


def _check_project_python_execution_ownership(root: Path) -> list[Violation]:
    """Compose narrow project/tooling/certification policy owners in stable order."""
    return [
        *check_project_tooling_contract(root),
        *check_project_supply_contract(root),
        *check_project_dependency_contract(root),
    ]
