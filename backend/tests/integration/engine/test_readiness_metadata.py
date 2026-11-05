"""Producer metadata failures preserve native readiness diagnostics."""

from pathlib import Path

import pytest

from oh_goon.core.models.operation import ProjectDeclarationUnavailable, RejectedUvProject
from oh_goon.engine.git import GitService
from oh_goon.engine.operations.readiness import readiness
from oh_goon.engine.project import policy_snapshot


def test_invalid_manifest_retains_native_uv_rejection_and_unavailable_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UV_NO_PROJECT", raising=False)
    monkeypatch.delenv("UV_PROJECT", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")

    checks, python, tools, _git = readiness(tmp_path, policy_snapshot(tmp_path), GitService())

    assert checks.project == "present"
    assert isinstance(tools.uv, RejectedUvProject)
    assert tools.uv.detail
    assert python.requires is None and python.selected is None
    assert isinstance(python.project_declaration, ProjectDeclarationUnavailable)
    assert python.project_declaration.producer_implementation_identity
    assert python.project_declaration.reason
