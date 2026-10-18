"""Executable M146 semantic vectors: no durable append or physical work performed."""

from dataclasses import FrozenInstanceError, replace
from itertools import permutations

import pytest

from oh_goon.core.enforcer_lace_semantics import (
    BaseResult,
    CanonicalOutcome as O,
    CommittedFact,
    Evaluation,
    FactKind as K,
    FrozenRules,
    ObservationClass as C,
    SemanticError,
    classify_step_outcome,
    derive_base_execution_result,
    envelope_at,
    evaluate,
    rollup_run,
    verify_committed_prefix,
)

RUN = "run-1"
STEP = "step-a"


def rules():
    return FrozenRules(
        observation_classes={"network.connect": C.AUTHORITY_BEARING, "progress.update": C.INFORMATIONAL},
        execution_permissions=frozenset({"network.connect"}),
        teardown_permissions=frozenset(),
    )


def facts(*items):
    return (CommittedFact(RUN, 1, K.RUN_ADMITTED),) + tuple(
        CommittedFact(RUN, i, k, step_id=s, observation=o, exit_code=code)
        for i, (k, s, o, code) in enumerate(items, start=2)
    )


def e(kind, step=STEP, observation=None, code=None):
    return (kind, step, observation, code)


def base(fs, *, step=STEP, prior_step_work_started=False):
    return derive_base_execution_result(fs, step_id=step, rules=rules(),
                                        prior_step_work_started=prior_step_work_started)


def classified(*items, proven=True, physical=True):
    fs = facts(*items, e(K.TEARDOWN_ENTERED), e(K.QUIESCENCE_PROVEN if proven else K.QUIESCENCE_UNPROVEN))
    return classify_step_outcome(fs, step_id=STEP, rules=rules(), physical_authority_existed=physical)


@pytest.mark.parametrize("events", [
    (), (CommittedFact(RUN, 2, K.RUN_ADMITTED),),
    (CommittedFact(RUN, 1, K.RUN_ADMITTED), CommittedFact(RUN, 3, K.ABORT_REQUESTED)),
    (CommittedFact(RUN, 1, K.RUN_ADMITTED), CommittedFact(RUN, 1, K.ABORT_REQUESTED)),
    (CommittedFact(RUN, 1, K.RUN_ADMITTED), CommittedFact("other", 2, K.ABORT_REQUESTED)),
    (CommittedFact(RUN, True, K.RUN_ADMITTED),),
    (CommittedFact(RUN, 1, K.RUN_ADMITTED), CommittedFact(RUN, 2, K.RUN_ADMITTED)),
    (CommittedFact(RUN, 1, K.RUN_ADMITTED), CommittedFact(RUN, 2, K.RUN_BINDING_FAILED, STEP)),
])
def test_run_sequencer_rejects_missing_gap_duplicate_reorder_cross_run_and_bool_seq(events):
    with pytest.raises(SemanticError):
        verify_committed_prefix(events)


def test_snapshot_rules_are_immutable_and_independent_of_caller_mutation():
    source = {"network.connect": C.AUTHORITY_BEARING}
    r = FrozenRules(source, frozenset({"network.connect"}), frozenset())
    source.clear()
    assert evaluate(r, "network.connect", envelope="execution") is Evaluation.PERMITTED
    with pytest.raises(TypeError):
        r.observation_classes["network.connect"] = C.INFORMATIONAL
    with pytest.raises(FrozenInstanceError):
        r.teardown_permissions = frozenset({"network.connect"})


@pytest.mark.parametrize("observation,envelope,expected", [
    ("network.connect", "execution", Evaluation.PERMITTED),
    ("network.connect", "teardown", Evaluation.VIOLATION),
    ("progress.update", "teardown", Evaluation.PERMITTED),
    ("future.security.event", "execution", Evaluation.EVALUATION_FAILED),
    ("", "execution", Evaluation.EVALUATION_FAILED),
])
def test_frozen_evaluator_is_total_for_known_unknown_and_teardown(observation, envelope, expected):
    assert evaluate(rules(), observation, envelope=envelope) is expected


def test_invalid_envelope_cannot_silently_apply_wider_permissions():
    with pytest.raises(SemanticError):
        evaluate(rules(), "network.connect", envelope="auto")


def test_sequencer_evaluates_observation_under_envelope_active_at_its_committed_seq():
    fs = facts(e(K.OBSERVED, observation="network.connect"), e(K.ABORT_REQUESTED),
               e(K.TEARDOWN_ENTERED), e(K.OBSERVED, observation="network.connect"))
    assert envelope_at(fs, 2, step_id=STEP) == "execution"
    assert envelope_at(fs, 5, step_id=STEP) == "teardown"
    assert base(fs).outcome is O.ENFORCEMENT_TERMINATED
    assert base(fs).cause_run_seq == 5


@pytest.mark.parametrize("first,second,expected", [
    (K.VIOLATION_MATCHED, K.ABORT_REQUESTED, O.ENFORCEMENT_TERMINATED),
    (K.ABORT_REQUESTED, K.VIOLATION_MATCHED, O.EXECUTION_ABORTED),
    (K.ABORT_REQUESTED, K.WORKLOAD_COMPLETED, O.EXECUTION_ABORTED),
    (K.WORKLOAD_COMPLETED, K.ABORT_REQUESTED, O.EXECUTION_COMPLETED),
    (K.WORKLOAD_FAILED, K.VIOLATION_MATCHED, O.EXECUTION_FAILED),
    (K.VIOLATION_MATCHED, K.WORKLOAD_FAILED, O.ENFORCEMENT_TERMINATED),
    (K.DEADLINE_EXPIRED, K.WORKLOAD_COMPLETED, O.EXECUTION_ABORTED),
    (K.WORKLOAD_COMPLETED, K.DEADLINE_EXPIRED, O.EXECUTION_COMPLETED),
    (K.STEP_BINDING_FAILED, K.ABORT_REQUESTED, O.STEP_BINDING_FAILED),
    (K.ABORT_REQUESTED, K.STEP_BINDING_FAILED, O.EXECUTION_ABORTED),
])
def test_first_durable_cause_wins_collision_vectors(first, second, expected):
    exit_kinds = {K.WORKLOAD_COMPLETED, K.WORKLOAD_FAILED}
    fs = facts(e(first, code=0 if first in exit_kinds else None),
               e(second, code=0 if second in exit_kinds else None))
    assert base(fs).outcome is expected
    assert base(fs).cause_run_seq == 2


def test_abort_after_step_work_is_execution_abort_not_prework():
    assert base(facts(e(K.STEP_WORK_STARTED), e(K.ABORT_REQUESTED))).outcome is O.EXECUTION_ABORTED
    assert base(facts(e(K.ABORT_REQUESTED)), prior_step_work_started=True).outcome is O.EXECUTION_ABORTED


def test_run_scoped_binding_and_cancellation_have_one_first_cause():
    binding_first = facts(e(K.RUN_BINDING_FAILED, step=None), e(K.ABORT_REQUESTED, step=None))
    abort_first = facts(e(K.ABORT_REQUESTED, step=None), e(K.RUN_BINDING_FAILED, step=None))
    assert base(binding_first, step=None).outcome is O.RUN_BINDING_FAILED
    assert base(abort_first, step=None).outcome is O.PREWORK_ABORTED
    assert rollup_run([], run_prework=base(binding_first, step=None),
                      run_teardown_proven=True, run_physical_authority_existed=True) is O.RUN_BINDING_FAILED


def test_first_step_binding_cancellation_is_step_abort_but_run_prework_abort():
    cancelled_step = classified(e(K.ABORT_REQUESTED))
    assert cancelled_step.outcome is O.EXECUTION_ABORTED
    assert rollup_run([cancelled_step], run_teardown_proven=True,
                      run_physical_authority_existed=True, any_step_work_started=False) is O.PREWORK_ABORTED
    assert rollup_run([cancelled_step], run_teardown_proven=True,
                      run_physical_authority_existed=True, any_step_work_started=True) is O.EXECUTION_ABORTED
    assert rollup_run([cancelled_step], run_teardown_proven=True,
                      run_physical_authority_existed=True) is None


def test_first_valid_failure_not_overridden_by_wall_clock_or_notification_order():
    fs = facts(e(K.VIOLATION_MATCHED), e(K.ABORT_REQUESTED))
    assert base(fs).cause_run_seq == 2
    with pytest.raises(SemanticError):
        base((fs[0], fs[2], fs[1]))


def test_unknown_authority_observation_fails_closed_even_if_no_violation_was_matched():
    fs = facts(e(K.OBSERVED, observation="new.file.access"))
    assert base(fs).outcome is O.ENFORCEMENT_TERMINATED


def test_no_completion_before_teardown_quiescence_evidence():
    beginning = facts(e(K.WORKLOAD_COMPLETED, code=0))
    assert classify_step_outcome(beginning, step_id=STEP, rules=rules(), physical_authority_existed=True) is None
    assert classify_step_outcome(beginning + (CommittedFact(RUN, 3, K.TEARDOWN_ENTERED, STEP),),
                                 step_id=STEP, rules=rules(), physical_authority_existed=True) is None
    done = classified(e(K.WORKLOAD_COMPLETED, code=0))
    assert done.outcome is O.EXECUTION_COMPLETED and done.containment_proven


@pytest.mark.parametrize("cause", [K.STEP_BINDING_FAILED, K.ABORT_REQUESTED,
                                  K.VIOLATION_MATCHED, K.WORKLOAD_FAILED, K.WORKLOAD_COMPLETED])
def test_unproven_quiescence_overrides_every_physical_base_cause(cause):
    code = 0 if cause is K.WORKLOAD_COMPLETED else 1 if cause is K.WORKLOAD_FAILED else None
    result = classified(e(cause, code=code), proven=False)
    assert result.outcome is O.CONTAINMENT_FAILED
    assert not result.containment_proven


def test_conflicting_cleanup_facts_rejected_not_silently_resolved():
    fs = facts(e(K.WORKLOAD_COMPLETED, code=0), e(K.TEARDOWN_ENTERED),
               e(K.QUIESCENCE_UNPROVEN), e(K.QUIESCENCE_PROVEN))
    with pytest.raises(SemanticError, match="conflicting"):
        classify_step_outcome(fs, step_id=STEP, rules=rules(), physical_authority_existed=True)


def test_quiescence_before_teardown_cannot_support_terminal_result():
    fs = facts(e(K.WORKLOAD_COMPLETED, code=0), e(K.QUIESCENCE_PROVEN), e(K.TEARDOWN_ENTERED))
    with pytest.raises(SemanticError, match="after teardown"):
        classify_step_outcome(fs, step_id=STEP, rules=rules(), physical_authority_existed=True)


def test_weak_negative_resource_evidence_does_not_prove_containment():
    assert classified(e(K.STEP_BINDING_FAILED), proven=False, physical=False) is None


def test_late_effect_after_proven_quiescence_invalidates_prior_proof():
    fs = facts(e(K.ABORT_REQUESTED), e(K.TEARDOWN_ENTERED),
               e(K.QUIESCENCE_PROVEN), e(K.OBSERVED, observation="network.connect"))
    with pytest.raises(SemanticError, match="after Step quiescence"):
        classify_step_outcome(fs, step_id=STEP, rules=rules(), physical_authority_existed=True)


def test_journal_append_failure_or_lease_loss_never_fabricates_terminal_result():
    for failure in (K.JOURNAL_APPEND_FAILED, K.CONTROL_LEASE_LOST):
        fs = facts(e(K.WORKLOAD_COMPLETED, code=0), e(failure, step=None),
                   e(K.TEARDOWN_ENTERED), e(K.QUIESCENCE_PROVEN))
        assert base(fs) is None
        assert classify_step_outcome(fs, step_id=STEP, rules=rules(), physical_authority_existed=True) is None


def test_run_rollup_preserves_first_blocking_failure_and_containment_override():
    success = classified(e(K.WORKLOAD_COMPLETED, code=0))
    failure = replace(classified(e(K.WORKLOAD_FAILED, code=3)), step_id="step-b")
    assert rollup_run([success], run_teardown_proven=True,
                      run_physical_authority_existed=True) is O.EXECUTION_COMPLETED
    assert rollup_run([success, failure], run_teardown_proven=True,
                      run_physical_authority_existed=True) is O.EXECUTION_FAILED
    assert rollup_run([success, failure], run_teardown_proven=False,
                      run_physical_authority_existed=True) is O.CONTAINMENT_FAILED
    with pytest.raises(SemanticError, match="after first blocking failure"):
        rollup_run([failure, success], run_teardown_proven=True, run_physical_authority_existed=True)
    with pytest.raises(SemanticError, match="duplicate Step"):
        rollup_run([success, success], run_teardown_proven=True, run_physical_authority_existed=True)


def test_outcome_and_rule_enumerations_are_closed_without_new_product_states():
    assert {item.value for item in O} == {
        "ADMISSION_REJECTED", "PREWORK_ABORTED", "RUN_BINDING_FAILED", "STEP_BINDING_FAILED",
        "EXECUTION_FAILED", "EXECUTION_ABORTED", "ENFORCEMENT_TERMINATED",
        "CONTAINMENT_FAILED", "EXECUTION_COMPLETED",
    }
    assert BaseResult(O.EXECUTION_FAILED, 2).outcome is O.EXECUTION_FAILED


def test_property_first_commit_is_first_cause_for_all_competing_base_facts():
    kinds = (K.WORKLOAD_COMPLETED, K.WORKLOAD_FAILED, K.ABORT_REQUESTED,
             K.DEADLINE_EXPIRED, K.VIOLATION_MATCHED, K.STEP_BINDING_FAILED)
    expected = {
        K.WORKLOAD_COMPLETED: O.EXECUTION_COMPLETED,
        K.WORKLOAD_FAILED: O.EXECUTION_FAILED,
        K.ABORT_REQUESTED: O.EXECUTION_ABORTED,
        K.DEADLINE_EXPIRED: O.EXECUTION_ABORTED,
        K.VIOLATION_MATCHED: O.ENFORCEMENT_TERMINATED,
        K.STEP_BINDING_FAILED: O.STEP_BINDING_FAILED,
    }
    for first, second in permutations(kinds, 2):
        exit_kinds = {K.WORKLOAD_COMPLETED, K.WORKLOAD_FAILED}
        fs = facts(e(first, code=0 if first in exit_kinds else None),
                   e(second, code=0 if second in exit_kinds else None))
        result = base(fs)
        assert result.outcome is expected[first]
        assert result.cause_run_seq == 2


def test_representation_invariance_across_policy_map_insertion_order():
    left = FrozenRules({"b": C.AUTHORITY_BEARING, "a": C.INFORMATIONAL}, frozenset({"b"}), frozenset())
    right = FrozenRules({"a": C.INFORMATIONAL, "b": C.AUTHORITY_BEARING}, frozenset({"b"}), frozenset())
    assert [evaluate(left, name, envelope="execution") for name in ("a", "b", "unknown")] == [
        evaluate(right, name, envelope="execution") for name in ("a", "b", "unknown")
    ]
