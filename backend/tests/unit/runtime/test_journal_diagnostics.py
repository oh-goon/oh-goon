from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRequest
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.journal_diagnostics import JournalDiagnosticsService, JournalReadProfile


def test_projection_profile_reports_one_cold_and_requested_warm_reads(tmp_path: Path) -> None:
    profile = JournalDiagnosticsService(LocalDatabase(tmp_path / "profile.db")).profile_reads(
        samples=3
    )
    assert profile.revision == 0
    assert profile.cold_milliseconds >= 0
    assert len(profile.warm_milliseconds) == 3
    assert "disposable derived state" in profile.render()


def test_projection_profile_rejects_unbounded_sample_count(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequest, match="between 1 and 100"):
        JournalDiagnosticsService(LocalDatabase(tmp_path / "profile.db")).profile_reads(samples=0)


def test_projection_profile_renders_empty_warm_sample_set() -> None:
    profile = JournalReadProfile(revision=7, cold_milliseconds=1.25, warm_milliseconds=())
    assert profile.warm_average_milliseconds == 0.0
    assert "Warm cached reads:  n/a ms" in profile.render()


def test_projection_profile_rejects_revision_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import dataclass
    from typing import cast

    from oh_goon.core.errors import StateConflict
    from oh_goon.runtime.journal import Journal
    from oh_goon.runtime.journal_view import JournalReadView

    @dataclass(frozen=True)
    class FakeView:
        revision: int

    revisions = iter((1, 2))

    def changed_read_view(self: Journal) -> JournalReadView:
        return cast(JournalReadView, FakeView(next(revisions)))

    monkeypatch.setattr(Journal, "read_view", changed_read_view)
    with pytest.raises(StateConflict, match="changed while profiling"):
        JournalDiagnosticsService(LocalDatabase(tmp_path / "profile.db")).profile_reads(samples=1)
