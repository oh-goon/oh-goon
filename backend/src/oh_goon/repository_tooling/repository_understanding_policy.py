"""Deterministic repository-understanding grading and qualification policy.

Graders emit observations. This module is the public grading authority: it maps observations
to a deterministic result while private helpers own the individual evaluation stages. It
intentionally contains no natural-language judgment.
"""

from __future__ import annotations

from typing import Any

from oh_goon.repository_tooling._repository_understanding_policy_aggregation import aggregate
from oh_goon.repository_tooling._repository_understanding_policy_evaluation import (
    EvaluationState,
    evaluate_question,
    validate_inputs,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    ASSERTION_RELATIONS as ASSERTION_RELATIONS,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    CANDIDATE_CONFIDENCE_STATES as CANDIDATE_CONFIDENCE_STATES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    CANDIDATE_EPISTEMIC_STATES as CANDIDATE_EPISTEMIC_STATES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    CLAIM_STATES as CLAIM_STATES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    DEFAULT_QUALIFICATION_PROFILE as DEFAULT_QUALIFICATION_PROFILE,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    DIMENSION_NORMALIZATION_POLICY_VERSION as DIMENSION_NORMALIZATION_POLICY_VERSION,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    DIMENSION_WEIGHTS as DIMENSION_WEIGHTS,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    QUALIFICATION_POLICY_VERSION as QUALIFICATION_POLICY_VERSION,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    QUALIFICATION_PROFILES as QUALIFICATION_PROFILES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    QUESTION_STATES as QUESTION_STATES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    REFERENCE_STATES as REFERENCE_STATES,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    SCORING_POLICY_VERSION as SCORING_POLICY_VERSION,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    qualification_policy_manifest as qualification_policy_manifest,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    qualification_profile,
)
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    weighted_harmonic as weighted_harmonic,
)
from oh_goon.repository_tooling._repository_understanding_policy_projection import (
    evaluation_result,
)
from oh_goon.repository_tooling.repository_understanding_graph import score_assertion_graph
from oh_goon.repository_tooling.repository_understanding_qualification_gates import (
    qualification_outcome,
)


def evaluate(
    *,
    exam_snapshot: dict[str, Any],
    observations: list[dict[str, Any]],
    evidence_validation: dict[str, dict[str, Any]],
    grader: dict[str, Any],
    graded_at_utc: str,
    qualification_profile_id: str = DEFAULT_QUALIFICATION_PROFILE,
) -> dict[str, Any]:
    """Apply policy to one frozen exam snapshot and structured grader observations."""

    profile = qualification_profile(qualification_profile_id)
    questions, by_id = validate_inputs(exam_snapshot, observations)
    state = EvaluationState(profile=profile)
    for question in questions:
        evaluate_question(
            state,
            question=question,
            observation=by_id[question["id"]],
            evidence_validation=evidence_validation,
        )
    aggregated = aggregate(
        state=state,
        questions=questions,
        exam_snapshot=exam_snapshot,
        score_graph=score_assertion_graph,
        qualification_gate=qualification_outcome,
    )
    return evaluation_result(
        state=state,
        aggregate=aggregated,
        qualification_profile_id=qualification_profile_id,
        grader=grader,
        graded_at_utc=graded_at_utc,
    )
