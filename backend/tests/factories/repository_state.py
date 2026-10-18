from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.git import GitService


def baseline_for(repository: Path | RepositoryIdentity) -> RepositoryStateFingerprint:
    path = repository.path if isinstance(repository, RepositoryIdentity) else repository
    return GitService().fingerprint(path)
