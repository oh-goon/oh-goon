from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.operation import (
    DetachedGitPosition,
    NamedGitPosition,
    ProjectDeclarationEvidence,
    ProjectDeclarationUnavailable,
    PythonRuntimeResult,
    ValidGitWorktreeInvariant,
    ViolatedGitWorktreeInvariant,
)
from oh_goon.engine.project import project_python, read_pyproject


def test_git_position_variants_make_branch_presence_structural() -> None:
    named = NamedGitPosition(state="named", branch="main")
    detached = DetachedGitPosition(state="detached")
    assert named.branch == "main"
    assert detached.branch is None
    with pytest.raises(ValidationError):
        NamedGitPosition(state="named", branch="")
    with pytest.raises(ValidationError):
        DetachedGitPosition(state="detached", branch="main")


def test_git_worktree_invariant_makes_unexpected_files_structural() -> None:
    assert ValidGitWorktreeInvariant(state="passed").unexpected_files == ()
    with pytest.raises(ValidationError):
        ValidGitWorktreeInvariant(state="passed", unexpected_files=("uv.lock",))
    with pytest.raises(ValidationError):
        ViolatedGitWorktreeInvariant(state="failed", unexpected_files=())
    assert ViolatedGitWorktreeInvariant(
        state="failed", unexpected_files=("uv.lock",)
    ).unexpected_files == ("uv.lock",)


def test_native_project_reader_uses_none_for_absent_python_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    result = project_python(tmp_path)
    assert result.requires is None
    assert result.pin is None
    assert result.selected is None
    assert isinstance(result.project_declaration, ProjectDeclarationEvidence)
    assert result.project_declaration.repository["freshness"] == "unknown"
    assert (
        result.project_declaration.provider["provenance"]["negative_evidence_admissible"] is False
    )
    assert read_pyproject(tmp_path)["project"]["name"] == "example"


def test_historical_python_runtime_payload_does_not_gain_a_metadata_claim() -> None:
    historical = {"requires": ">=3.12", "pin": None, "selected": None}
    assert PythonRuntimeResult.model_validate(historical).model_dump(mode="json") == historical


def test_authored_python_metadata_does_not_resolve_an_environment(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    (tmp_path / ".python-version").write_text("3.13\n")
    result = project_python(tmp_path)
    assert result.requires == ">=3.12"
    assert result.pin == "3.13"
    assert result.selected is None
    assert isinstance(result.project_declaration, ProjectDeclarationEvidence)
    group = result.project_declaration.declaration_group
    assert group is not None
    assert group["coverage"]["state"] == "incomplete"
    assert group["declarations"][0]["value"]["native_interpretation"] == "not-established"


def test_missing_python_manifest_keeps_unobserved_metadata_explicit(tmp_path: Path) -> None:
    result = project_python(tmp_path)
    assert result.requires is None
    assert isinstance(result.project_declaration, ProjectDeclarationEvidence)
    assert result.project_declaration.declaration_group is None
    assert result.project_declaration.provider["provenance"]["missing_selected_paths"] == [
        "pyproject.toml"
    ]


def test_invalid_python_manifest_does_not_become_empty_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\n")
    (tmp_path / ".python-version").write_text("3.13\n")
    result = project_python(tmp_path)
    assert result.requires is None
    assert result.pin == "3.13"
    assert isinstance(result.project_declaration, ProjectDeclarationUnavailable)
    assert result.project_declaration.reason
    assert result.project_declaration.producer_implementation_identity


def test_missing_mandatory_producer_does_not_become_unavailable_optional_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine import project
    from oh_goon.integrations.hashmarks import HashmarksRuntimeContractError

    def missing_runtime(repository: Path) -> None:
        raise HashmarksRuntimeContractError("mandatory producer unavailable")

    monkeypatch.setattr(project, "observe_python_project_declarations", missing_runtime)
    with pytest.raises(HashmarksRuntimeContractError, match="mandatory producer unavailable"):
        project_python(tmp_path)


def test_python_readiness_discloses_metadata_without_unrelated_source_configuration(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="example"\nrequires-python=">=3.12"\n'
        '[tool.uv.sources]\nprivate = { git = "https://private-token@example.test/code.git" }\n'
    )
    result = project_python(tmp_path)
    assert result.requires == ">=3.12"
    assert isinstance(result.project_declaration, ProjectDeclarationEvidence)
    assert result.project_declaration.observation_identity
    assert result.project_declaration.provider["inputs"][0]["member_revision"]
    assert result.project_declaration.disclosure == "python-project-declaration-only"
    assert "private-token" not in result.model_dump_json()
