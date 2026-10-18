from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.runtime.execution_timeline_read import project_bounded_execution_timeline


def _repo(tmp_path: Path, name: str) -> RepositoryIdentity:
    path = tmp_path / name
    path.mkdir()
    return RepositoryIdentity(path=path)


def test_execution_timeline_projects_only_repository_page(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    alpha, beta, gamma = (_repo(tmp_path, name) for name in ("alpha", "beta", "gamma"))
    details = {}
    admitted = []
    for revision, repository in enumerate((alpha, beta, gamma), start=1):
        run = SimpleNamespace(
            accepted_at=now,
            finished_at=now + timedelta(seconds=1),
            target=None,
            targets=(repository,),
        )
        details[f"run-{revision}"] = SimpleNamespace(run=run)
        admitted.append((revision, f"run-{revision}"))
    index = SimpleNamespace(admitted_revisions=tuple(admitted), run_details_by_id=details)
    projected: list[tuple[object, ...]] = []

    def record(selected):
        projected.append(selected)
        return ()

    monkeypatch.setattr(
        "oh_goon.runtime.execution_timeline_read.project_repository_run_observations", record
    )
    page = project_bounded_execution_timeline(
        index,
        journal_revision=3,
        from_at=now - timedelta(seconds=1),
        to_at=now + timedelta(seconds=2),
        bucket="day",
        plan_query=None,
        repository_query=None,
        offset=1,
        limit=1,
    )

    assert page.total_repositories == 3
    assert page.offset == 1
    assert len(projected) == 1
    assert projected[0] == (details["run-2"],)
