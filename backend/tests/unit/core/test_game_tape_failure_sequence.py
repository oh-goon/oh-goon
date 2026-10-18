from __future__ import annotations

from pathlib import Path

from oh_goon.core.game_tape_failure_history import project_game_tape_failure_history
from oh_goon.core.models.repository import RepositoryIdentity
from tests.unit.core.test_game_tape_failure_history import _failed_detail, _passed_detail


def _history(current, prior=(), later=(), *, sample_limit: int = 10):
    tape = current.game_tape
    repository_id = tape.repositories[0].repository.repository_id
    return project_game_tape_failure_history(
        tape,
        tuple(item.game_tape for item in prior),
        tuple(item.game_tape for item in later),
        repository_id=repository_id,
        step_id="test",
        sample_limit=sample_limit,
    )


def test_failure_sequence_is_bounded_around_selected_run(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior_near = _failed_detail(repository, run_id="run-prior-near", stderr="different\n")
    prior_far = _passed_detail(repository, run_id="run-prior-far")
    later_near = _passed_detail(repository, run_id="run-later-near")
    later_far = _failed_detail(repository, run_id="run-later-far", stderr="same\n")

    sequence = _history(
        current,
        (prior_near, prior_far),
        (later_near, later_far),
        sample_limit=1,
    ).sequence

    assert sequence.sample_rule == "NEAREST_COMPARABLE_SAME_PLAN_REPOSITORY_STEP"
    assert [entry.fleet_run_id for entry in sequence.entries] == [
        "run-prior-near",
        "run-current",
        "run-later-near",
    ]
    assert [entry.position for entry in sequence.entries] == ["PRIOR", "SELECTED", "LATER"]
    assert [entry.signature_relation for entry in sequence.entries] == [
        "DIFFERENT_SIGNATURE",
        "EXACT_SIGNATURE",
        "NO_SIGNATURE",
    ]
    assert sequence.summary.prior_comparable_runs == 2
    assert sequence.summary.later_comparable_runs == 2
    assert sequence.summary.sampled_prior_runs == 1
    assert sequence.summary.sampled_later_runs == 1
    assert sequence.summary.latest_comparable_run_id == "run-later-far"
    assert sequence.summary.latest_signature_relation == "EXACT_SIGNATURE"


def test_failure_sequence_reports_same_context_pass_and_recurrence_only_as_facts(
    tmp_path: Path,
) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    changed_pass = _passed_detail(
        repository,
        run_id="run-pass-changed",
        workspace_sha256="b" * 64,
    )
    same_pass = _passed_detail(repository, run_id="run-pass-same")
    same_recurrence = _failed_detail(repository, run_id="run-recurred", stderr="same\n")

    summary = _history(
        current,
        later=(changed_pass, same_pass, same_recurrence),
    ).sequence.summary

    assert summary.later_same_context_passes == 1
    assert summary.first_later_same_context_pass_run_id == "run-pass-same"
    assert summary.later_same_context_exact_failures == 1
    assert summary.exact_failure_recurred_in_same_context_after_same_context_pass is True
    assert summary.latest_comparable_run_id == "run-recurred"
    assert summary.latest_context_classification == "SAME_CONTEXT"


def test_failure_sequence_context_coverage_is_explicit(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior = _failed_detail(repository, run_id="run-prior", stderr="same\n")

    summary = _history(current, prior=(prior,)).sequence.summary

    assert summary.complete_context_entries + summary.incomplete_context_entries == 2
    assert summary.complete_context_entries == 2
    assert summary.incomplete_context_entries == 0
