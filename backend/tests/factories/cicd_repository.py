from __future__ import annotations

from pathlib import Path


def seed_cicd_repository(repository: Path) -> None:
    """Write the smallest repository that satisfies the strict Python/uv CI/CD baseline."""
    repository.mkdir(parents=True, exist_ok=True)
    (repository / ".git").mkdir(exist_ok=True)
    (repository / "pyproject.toml").write_text(
        """[project]\nname = \"fixture\"\nversion = \"0.0.0\"\n\n[tool.oh-goon.validation]\nlint = [\"lint\"]\nformat = [\"format\"]\ntests = [\"tests\"]\n""",
        encoding="utf-8",
    )
    (repository / ".pre-commit-config.yaml").write_text(
        """repos:\n  - repo: local\n    hooks:\n      - id: lint\n        name: lint\n        entry: true\n        language: system\n      - id: format\n        name: format\n        entry: true\n        language: system\n      - id: tests\n        name: tests\n        entry: true\n        language: system\n""",
        encoding="utf-8",
    )
    ci = repository / "ci"
    ci.mkdir(exist_ok=True)
    for name in ("prepare.sh", "build.sh", "publish.sh"):
        (ci / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
