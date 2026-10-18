from __future__ import annotations

from pathlib import Path

import oh_goon.workflow as workflow
from oh_goon.repository_tooling import native_real_lifecycle_artifact as artifact_lifecycle


def test_normal_certification_does_not_require_or_create_git(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    expected = ("repo-source", {"A": "B"}, tmp_path / "locks")
    monkeypatch.setattr(
        workflow,
        "_certification_environment",
        lambda: expected,
    )

    assert workflow._prepare_plan_context(command="certify") == expected
    assert not (tmp_path / ".git").exists()
    assert capsys.readouterr().out == ""


def test_release_does_not_require_git_before_release_authority(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    expected = ("repo-source", {"A": "B"}, tmp_path / "locks")
    monkeypatch.setattr(
        workflow,
        "_certification_environment",
        lambda *, require_strict_executor=False: expected,
    )
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        workflow,
        "_run_release_with_certification_state",
        lambda **kwargs: seen.update(kwargs) or 0,
    )

    monkeypatch.setattr(workflow, "_certification_product_version", lambda: "1267.0.1055")
    assert workflow.run_release() == 0
    assert seen == {
        "repo_id": "repo-source",
        "env": {"A": "B"},
        "lock_root": tmp_path / "locks",
    }
    assert not (tmp_path / ".git").exists()
    assert capsys.readouterr().err == ""

def test_source_zip_acceptance_does_not_manufacture_git_authority() -> None:
    source = Path(artifact_lifecycle.__file__).read_text(encoding="utf-8")
    assert "_prepare_synthetic_git_fixture" not in source
    assert '"git", "init"' not in source
    assert "OH_GOON_ACCEPTANCE_GIT_FIXTURE" not in source
