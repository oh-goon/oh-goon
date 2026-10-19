from __future__ import annotations

import tomllib
from pathlib import Path

from oh_goon.core.models.migration import MigrationCandidate
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import repository_instance_id
from oh_goon.core.vocabulary import MigrationReadiness, MigrationSource


def detect_migration_candidate(repository: Path) -> MigrationCandidate:
    repository = repository.expanduser().resolve()
    pyproject = repository / "pyproject.toml"
    requirements_in = repository / "requirements.in"
    requirements_txt = repository / "requirements.txt"
    source_files: list[str] = []
    if (repository / "uv.lock").exists():
        return _candidate(
            repository, MigrationSource.UV, MigrationReadiness.ALREADY_UV, ["uv.lock"]
        )
    if pyproject.exists():
        source_files.append("pyproject.toml")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return _candidate(
                repository,
                MigrationSource.PYPROJECT,
                MigrationReadiness.REVIEW_REQUIRED,
                source_files,
                "pyproject.toml could not be parsed safely",
            )
        tool = data.get("tool") if isinstance(data, dict) else None
        tool = tool if isinstance(tool, dict) else {}
        if "poetry" in tool:
            return _candidate(
                repository,
                MigrationSource.POETRY,
                MigrationReadiness.REVIEW_REQUIRED,
                source_files,
                "Poetry metadata requires review before conversion",
            )
        if "pdm" in tool:
            return _candidate(
                repository,
                MigrationSource.PDM,
                MigrationReadiness.REVIEW_REQUIRED,
                source_files,
                "PDM metadata requires review before conversion",
            )
        if "rye" in tool:
            return _candidate(
                repository,
                MigrationSource.RYE,
                MigrationReadiness.REVIEW_REQUIRED,
                source_files,
                "Rye metadata requires review before conversion",
            )
        if isinstance(data.get("project"), dict):
            return _candidate(
                repository,
                MigrationSource.PYPROJECT,
                MigrationReadiness.SAFE_AUTOMATIC,
                source_files,
                "PEP 621 project can be locked directly by uv",
            )
        return _candidate(
            repository,
            MigrationSource.PYPROJECT,
            MigrationReadiness.REVIEW_REQUIRED,
            source_files,
            "pyproject.toml has no PEP 621 [project] table",
        )
    if requirements_in.exists():
        source_files = ["requirements.in"]
        if requirements_txt.exists():
            source_files.append("requirements.txt")
        return _candidate(
            repository,
            MigrationSource.PIP_TOOLS,
            MigrationReadiness.SAFE_AUTOMATIC,
            source_files,
            "requirements.in can be imported with the compiled requirements as a constraint",
        )
    requirement_files = sorted(
        path.name for path in repository.glob("requirements*.txt") if path.is_file()
    )
    if requirement_files:
        return _candidate(
            repository,
            MigrationSource.PIP_REQUIREMENTS,
            MigrationReadiness.REVIEW_REQUIRED,
            requirement_files,
            "Pinned requirements need an explicit direct-dependency review",
        )
    return _candidate(
        repository,
        MigrationSource.UNKNOWN,
        MigrationReadiness.UNSUPPORTED,
        [],
        "No supported Python dependency manifest was found",
    )


def _candidate(
    repository: Path,
    source: MigrationSource,
    readiness: MigrationReadiness,
    files: list[str],
    reason: str | None = None,
) -> MigrationCandidate:
    return MigrationCandidate(
        repository=RepositoryIdentity(name=repository.name, path=repository),
        repository_instance_id=repository_instance_id(repository),
        source=source,
        readiness=readiness,
        reasons=[reason] if reason else [],
        source_files=files,
    )
