from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._launch_contract_common import (
    forbidden_import_violations,
    owned_string_constant_violations,
)
from oh_goon.repository_tooling.architecture.common import Violation


def check_launch_navigation(root: Path) -> list[Violation]:
    """Keep navigation projection free of process/browser authority; behavior is executable-tested."""

    navigation = root / "backend/src/oh_goon/core/launch_navigation.py"
    violations = owned_string_constant_violations(
        root,
        {
            root / "backend/src/oh_goon/runtime/local_launch_access.py": (
                "LOCAL_LAUNCH_ACCESS_SCHEMA",
            ),
        },
        missing_message="durable local-launch access schema owner is missing or invalid",
        duplicate_message="local-launch access schema must have one natural owner",
    )
    violations.extend(
        forbidden_import_violations(
            navigation,
            ("subprocess", "webbrowser"),
            message_prefix="navigation projection must never gain browser/process authority",
        )
    )
    return violations
