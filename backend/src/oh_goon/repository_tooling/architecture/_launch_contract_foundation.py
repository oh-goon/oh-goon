from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._launch_contract_common import (
    owned_string_constant_violations,
    forbidden_import_violations,
)
from oh_goon.repository_tooling.architecture.common import Violation


def _launch_ownership_document(root: Path) -> Path:
    return root / "docs/development/LAUNCH_OWNERSHIP.md"


def _documentation_violations(root: Path) -> list[Violation]:
    """Require the Launch ownership guide to exist without making prose machine authority.

    Runtime/schema/import contracts below are the executable authority. Documentation may be
    clarified or reworded without changing those semantics, so architecture intentionally does
    not match sentences, helper names, or other prose fragments here.
    """

    path = _launch_ownership_document(root)
    if not path.is_file():
        return [Violation(path, "Launch ownership documentation is missing")]
    return []


def check_launch_foundation_model(root: Path) -> list[Violation]:
    """Keep Launch public schema identities explicit and read models free of process authority."""

    model = root / "backend/src/oh_goon/core/launch_manifest.py"
    events = root / "backend/src/oh_goon/core/launch_events.py"
    projection = root / "backend/src/oh_goon/core/launch_projection.py"
    process_birth = root / "backend/src/oh_goon/core/process_birth.py"
    violations = _documentation_violations(root)
    violations.extend(
        owned_string_constant_violations(
            root,
            {
                model: ("LAUNCH_MANIFEST_SCHEMA", "LAUNCH_MANIFEST_KIND"),
                events: ("LAUNCH_EVENT_SCHEMA", "LAUNCH_EVENT_KIND"),
            },
            missing_message="Launch Manifest/Event identity owner is missing or invalid",
            duplicate_message="Launch Manifest/Event identity must have one natural owner",
        )
    )
    for path in (model, events, projection, process_birth):
        violations.extend(
            forbidden_import_violations(
                path,
                ("subprocess", "uvicorn"),
                message_prefix="Launch Manifest foundation must remain read-only and must not absorb process authority",
            )
        )
    return violations


def check_launch_projection_boundary(root: Path) -> list[Violation]:
    """Game Tape may consume admitted launch evidence but never live launch runtime state."""

    game_tape = root / "backend/src/oh_goon/core/game_tape_projection.py"
    forbidden_prefixes = (
        "oh_goon.runtime.local_launch_state",
        "oh_goon.runtime.local_launch_context",
        "oh_goon.runtime.local_launch_binding",
    )
    return forbidden_import_violations(
        game_tape,
        forbidden_prefixes,
        message_prefix="Game Tape must consume admitted launch evidence and never read live launch runtime state",
    )
