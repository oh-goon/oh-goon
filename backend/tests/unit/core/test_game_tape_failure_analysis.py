from __future__ import annotations

from pathlib import Path

import oh_goon.core.game_tape_failure_analysis as failure_analysis
from oh_goon.core.models.repository import RepositoryIdentity
from tests.unit.core.test_game_tape_failure_history import _failed_detail, _passed_detail


def test_failure_analysis_interprets_each_comparable_selected_context_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior = _failed_detail(repository, run_id="run-prior", stderr="same\n")
    later = _passed_detail(repository, run_id="run-later")
    calls = 0
    original = failure_analysis.compare_reproduction_context

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(failure_analysis, "compare_reproduction_context", counted)
    tape = current.game_tape
    repository_id = tape.repositories[0].repository.repository_id

    analysis = failure_analysis.analyze_failure_history(
        current.game_tape,
        (prior.game_tape,),
        (later.game_tape,),
        repository_id=repository_id,
        step_id="test",
        sample_limit=1,
    )

    assert calls == 3  # selected self + one prior + one later
    assert analysis.current.signature_relation == "EXACT_SIGNATURE"
    assert analysis.prior[0].selected_context.summary.classification == "SAME_CONTEXT"
    assert analysis.later[0].signature_relation == "NO_SIGNATURE"


def test_failure_analysis_preserves_journal_window_order(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior_near = _failed_detail(repository, run_id="run-prior-near", stderr="same\n")
    prior_far = _passed_detail(repository, run_id="run-prior-far")
    later_near = _passed_detail(repository, run_id="run-later-near")
    later_far = _failed_detail(repository, run_id="run-later-far", stderr="same\n")
    tape = current.game_tape
    repository_id = tape.repositories[0].repository.repository_id

    analysis = failure_analysis.analyze_failure_history(
        current.game_tape,
        (prior_near.game_tape, prior_far.game_tape),
        (later_near.game_tape, later_far.game_tape),
        repository_id=repository_id,
        step_id="test",
        sample_limit=1,
    )

    assert [item.run_id for item in analysis.prior] == [
        "run-prior-near",
        "run-prior-far",
    ]
    assert [item.run_id for item in analysis.later] == [
        "run-later-near",
        "run-later-far",
    ]


def test_failure_analysis_builds_adjacent_transitions_once_and_samples_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import oh_goon.core.game_tape_failure_fact as failure_fact
    from oh_goon.core.game_tape_failure_sequence import project_failure_sequence

    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _failed_detail(repository, run_id="run-current", stderr="same\n")
    prior = _passed_detail(repository, run_id="run-prior")
    later = _passed_detail(repository, run_id="run-later")
    tape = current.game_tape
    repository_id = tape.repositories[0].repository.repository_id
    calls = 0
    original = failure_fact.compare_reproduction_context

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(failure_fact, "compare_reproduction_context", counted)
    analysis = failure_analysis.analyze_failure_history(
        current.game_tape,
        (prior.game_tape,),
        (later.game_tape,),
        repository_id=repository_id,
        step_id="test",
        sample_limit=1,
    )

    assert calls == 2
    assert [item.run_id for item in analysis.chronological] == [
        "run-prior",
        "run-current",
        "run-later",
    ]
    assert [(item.from_run_id, item.to_run_id) for item in analysis.sample.transitions] == [
        ("run-prior", "run-current"),
        ("run-current", "run-later"),
    ]

    def unexpected(*args, **kwargs):
        raise AssertionError("sequence projection must not compare reproduction context again")

    monkeypatch.setattr(failure_fact, "compare_reproduction_context", unexpected)
    sequence = project_failure_sequence(analysis, sample_limit=1)
    assert len(sequence.transitions) == 2
