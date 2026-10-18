from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._launch_contract_common import (
    owned_string_constant_violations,
)
from oh_goon.repository_tooling.architecture.common import Violation


def check_launch_status_schemas(root: Path) -> list[Violation]:
    """Persisted launch binding/status schema identities are public durable contracts."""

    return owned_string_constant_violations(
        root,
        {
            root / "backend/src/oh_goon/runtime/local_launch_binding.py": ("LOCAL_LAUNCH_BINDING_SCHEMA",),
            root / "backend/src/oh_goon/core/launch_status.py": ("LAUNCH_STATUS_SCHEMA",),
            root / "backend/src/oh_goon/cli_status.py": ("LAUNCH_STATUS_ERROR_SCHEMA",),
        },
        missing_message="durable Launch status identity owner is missing or invalid",
        duplicate_message="Launch status identity must have one natural owner",
    )
