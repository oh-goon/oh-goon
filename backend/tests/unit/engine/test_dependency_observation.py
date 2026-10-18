from pathlib import Path

import pytest

from oh_goon.engine.dependency_observation import observe_python_dependencies


def _write_repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        """[project]\nname='sample'\ndependencies=['MLflow>=2', 'requests']\n\n[tool.uv.workspace]\nmembers=['packages/*']\n""",
        encoding="utf-8",
    )
    member = tmp_path / "packages" / "worker"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        """[project]\nname='worker'\ndependencies=['numpy>=2']\n""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """version = 1\n[[package]]\nname='mlflow'\nversion='2.17.2'\n[[package]]\nname='requests'\nversion='2.32.3'\n[[package]]\nname='urllib3'\nversion='2.2.3'\n[[package]]\nname='numpy'\nversion='2.1.0'\n""",
        encoding="utf-8",
    )
    return tmp_path


def test_dependency_observation_is_complete_typed_snapshot(tmp_path: Path) -> None:
    snapshot = observe_python_dependencies(_write_repo(tmp_path))
    by_name = {item.package_name: item for item in snapshot.dependencies}
    assert snapshot.coverage == "complete"
    assert {source.path for source in snapshot.sources} == {
        "pyproject.toml",
        "packages/worker/pyproject.toml",
        "uv.lock",
    }
    assert by_name["mlflow"].resolved_version == "2.17.2"
    assert by_name["mlflow"].declared_range == ">=2"
    assert by_name["mlflow"].relationship == "direct"
    assert by_name["urllib3"].relationship == "transitive"
    assert by_name["urllib3"].declared_range is None
    assert by_name["numpy"].declared_source == "packages/worker/pyproject.toml"


def test_dependency_observation_rejects_partial_snapshot(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="uv.lock"):
        observe_python_dependencies(tmp_path)
