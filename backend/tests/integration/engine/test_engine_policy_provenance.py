from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.models.operation import ReadinessOperationEvidence
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.git import GitService
from oh_goon.engine.runner import EngineRunner
from tests.factories.capabilities import capability_request, execute_capability_runner


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True, text=True)


def test_init_report_binds_effective_policy_and_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-q", "-b", "main")
    git(repository, "config", "user.email", "test@example.com")
    git(repository, "config", "user.name", "Test")
    (repository / "pyproject.toml").write_text(
        """[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n\n[tool.uv]\nresolution = "lowest-direct"\nprerelease = "allow"\nrequired-version = ">=0.10.0"\n""",
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text(
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n', encoding="utf-8"
    )
    git(repository, "add", "pyproject.toml", "uv.lock")
    git(repository, "commit", "-qm", "initial")

    monkeypatch.setenv("OH_GOON_REQUIRE_REMOTE", "false")
    monkeypatch.setenv("UV_RESOLUTION", "highest")
    monkeypatch.delenv("OH_GOON_RESOLUTION", raising=False)
    monkeypatch.delenv("UV_PRERELEASE", raising=False)
    monkeypatch.delenv("OH_GOON_PRERELEASE", raising=False)

    request = capability_request(repository, OperationAction.INIT, operation_id="op-policy")
    report = execute_capability_runner(
        EngineRunner(GitService(), timeout_seconds=10), request
    ).report

    assert isinstance(report.evidence, ReadinessOperationEvidence)
    assert report.evidence.policy is not None
    policy = report.evidence.policy
    assert report.policy == policy
    assert policy.values.require_remote is False
    values = policy.values.model_dump(mode="json")
    sources = policy.sources.model_dump(mode="json")
    for native_uv_field in (
        "resolution",
        "prerelease",
        "exclude_newer",
        "index_policy",
        "uv_required_version",
    ):
        assert native_uv_field not in values
        assert native_uv_field not in sources
