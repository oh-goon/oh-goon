from __future__ import annotations

from tests.factories.cicd_repository import seed_cicd_repository


def test_seed_cicd_repository_is_idempotent_when_ci_directory_already_exists(tmp_path) -> None:
    repository = tmp_path / "repository"
    ci = repository / "ci"
    ci.mkdir(parents=True)

    seed_cicd_repository(repository)
    seed_cicd_repository(repository)

    assert sorted(path.name for path in ci.iterdir()) == [
        "build.sh",
        "prepare.sh",
        "publish.sh",
    ]
