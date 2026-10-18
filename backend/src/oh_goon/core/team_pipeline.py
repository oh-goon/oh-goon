from __future__ import annotations

from pathlib import Path

from oh_goon.core.product_identity import canonical_repository_path

TEAM_PIPELINE_FILENAME = "goon.yml"


def team_pipeline_path(repository: Path | str) -> Path:
    """Return the one canonical repository-owned CI/CD entrypoint."""
    return canonical_repository_path(repository) / TEAM_PIPELINE_FILENAME
