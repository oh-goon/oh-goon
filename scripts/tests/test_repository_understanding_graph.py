from __future__ import annotations

from oh_goon.repository_tooling.repository_understanding_graph import (
    EXPECTED_EDGES,
    evidence_authority_class,
    evidence_expectation_roles,
    evidence_profile,
    graph_manifest,
    score_assertion_graph,
)


def _assertion(edge, polarity="AFFIRMED"):
    return {"question_id":"Q01","subject":edge["subject"],"relation":edge["relation"],"object":edge["object"],"polarity":polarity}


def test_expected_graph_has_critical_edges_and_stable_vocabulary() -> None:
    manifest = graph_manifest()
    assert manifest["expected_edge_count"] >= 15
    assert manifest["critical_expected_edge_count"] >= 10
    assert "owns" in manifest["relations"]
    assert "must_not_own" in manifest["relations"]


def test_graph_scores_exact_expected_edges_at_full_recall() -> None:
    result = score_assertion_graph([_assertion(edge) for edge in EXPECTED_EDGES])
    assert result["recall_percent"] == 100.0
    assert result["critical_recall_percent"] == 100.0
    assert result["missing_critical_edges"] == []
    assert result["forbidden_affirmed_edges"] == []


def test_forbidden_relationship_is_detected_independently_of_prose_score() -> None:
    assertions = [_assertion(edge) for edge in EXPECTED_EDGES]
    assertions.append({"question_id":"Q99","subject":"Plan","relation":"owns","object":"Step execution","polarity":"AFFIRMED"})
    result = score_assertion_graph(assertions)
    assert ["Plan", "owns", "Step execution"] in result["forbidden_affirmed_edges"]


def test_evidence_authority_and_independence_are_path_derived() -> None:
    assert evidence_authority_class("backend/src/oh_goon/runtime/database.py:1-10") == "A1"
    assert evidence_authority_class("backend/tests/unit/runtime/test_database.py:1-10") == "A2"
    assert evidence_authority_class("generated/openapi/openapi.json:1-10") == "A0"
    assert evidence_authority_class("AGENTS.md:1-10") == "A4"
    profile = evidence_profile([
        "backend/src/oh_goon/runtime/database.py:1-10",
        "backend/tests/unit/runtime/test_database.py:1-10",
    ])
    assert profile["independent_authority_classes"] == 2
    assert profile["authority_classes"] == ["A1", "A2"]


def test_secret_evidence_roles_separate_required_alternative_and_supplementary() -> None:
    roles = evidence_expectation_roles(
        [
            "backend/src/oh_goon/runtime/database.py:1-10",
            "backend/tests/unit/runtime/test_database.py:1-10",
            "docs/architecture.md:1-10",
        ],
        "A2",
    )
    assert roles["minimum_authority"] == "A2"
    assert roles["required_authority"][0]["authority"] == "A1"
    assert any(item["authority"] == "A2" for item in roles["sufficient_alternatives"])
    assert any(item["authority"] == "A5" for item in roles["supplementary"])
    assert roles["independent_authority_classes_available"] == 2


def test_graph_reports_concept_level_scores_and_sample_coverage() -> None:
    result = score_assertion_graph([_assertion(edge) for edge in EXPECTED_EDGES])
    assert result["sample_coverage"]["status"] == "ENOUGH"
    assert result["sample_coverage"]["coverage_percent"] == 100.0
    assert result["concept_scores"]
    assert all(row["sampled"] for row in result["concept_scores"].values())
    assert all(row["recall_percent"] == 100.0 for row in result["concept_scores"].values())


def test_graph_sample_coverage_fails_closed_when_too_few_concepts_are_observed() -> None:
    first_concept = EXPECTED_EDGES[0]["concept"]
    assertions = [_assertion(edge) for edge in EXPECTED_EDGES if edge["concept"] == first_concept]
    result = score_assertion_graph(assertions)
    assert result["sample_coverage"]["status"] == "NOT_ENOUGH_EVIDENCE"
    assert result["status"] == "NOT_ENOUGH_EVIDENCE"
    assert result["sample_coverage"]["sampled_concepts"] == 1


def test_graph_concept_score_surfaces_missing_critical_relationships() -> None:
    target = next(edge for edge in EXPECTED_EDGES if edge["critical"])
    assertions = [_assertion(edge) for edge in EXPECTED_EDGES if edge is not target]
    result = score_assertion_graph(assertions)
    concept = result["concept_scores"][target["concept"]]
    assert list((target["subject"], target["relation"], target["object"])) in concept["missing_critical_edges"]
    assert concept["critical_recall_percent"] < 100.0
