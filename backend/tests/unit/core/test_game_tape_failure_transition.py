from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.repository import RepositoryIdentity
from tests.unit.core.test_game_tape_failure_history import _failed_detail, _passed_detail
from tests.unit.core.test_game_tape_failure_sequence import _history


def test_failure_sequence_projects_adjacent_transition_facts(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    prior_pass = _passed_detail(repository, run_id="run-prior-pass", duration_ms=1200)
    current = _failed_detail(
        repository,
        run_id="run-current",
        stderr="same\n",
        duration_ms=3000,
    )
    later_changed_pass = _passed_detail(
        repository,
        run_id="run-later-pass",
        duration_ms=1700,
        workspace_sha256="b" * 64,
    )

    sequence = _history(
        current,
        prior=(prior_pass,),
        later=(later_changed_pass,),
    ).sequence

    assert [(item.from_run_id, item.to_run_id) for item in sequence.transitions] == [
        ("run-prior-pass", "run-current"),
        ("run-current", "run-later-pass"),
    ]
    first, second = sequence.transitions
    assert first.status_changed is True
    assert first.context.summary.classification == "SAME_CONTEXT"
    assert first.duration_delta_ms == 1800
    assert first.from_signature_relation == "NO_SIGNATURE"
    assert first.to_signature_relation == "EXACT_SIGNATURE"
    assert first.signature_relation_changed is True

    assert second.status_changed is True
    assert second.context.summary.classification == "CHANGED_CONTEXT"
    assert second.context.workspace == "DIFFERENT"
    assert second.duration_delta_ms == -1300

    summary = sequence.transition_summary
    assert summary.sampled_transition_count == 2
    assert summary.status_change_count == 2
    assert summary.same_context_status_changes == 1
    assert summary.changed_context_status_changes == 1
    assert summary.incomplete_context_status_changes == 0
    assert summary.signature_relation_change_count == 2


def test_selected_context_window_stops_at_nearest_non_matching_context(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior_near_same = _failed_detail(repository, run_id="run-prior-same", stderr="same\n")
    prior_boundary = _failed_detail(
        repository,
        run_id="run-prior-boundary",
        stderr="same\n",
        workspace_sha256="b" * 64,
    )
    later_near_same = _passed_detail(repository, run_id="run-later-same")
    later_boundary = _passed_detail(
        repository,
        run_id="run-later-boundary",
        workspace_sha256="c" * 64,
    )
    later_after_boundary_same = _passed_detail(repository, run_id="run-later-after-boundary")

    window = _history(
        current,
        prior=(prior_near_same, prior_boundary),
        later=(later_near_same, later_boundary, later_after_boundary_same),
    ).sequence.selected_context_window

    assert window.prior_same_context_runs == 1
    assert window.later_same_context_runs == 1
    assert window.total_same_context_runs_including_selected == 3
    assert window.nearest_prior_boundary_run_id == "run-prior-boundary"
    assert window.nearest_prior_boundary_classification == "CHANGED_CONTEXT"
    assert window.nearest_later_boundary_run_id == "run-later-boundary"
    assert window.nearest_later_boundary_classification == "CHANGED_CONTEXT"
