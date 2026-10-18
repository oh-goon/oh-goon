"""SECRET repository-relationship graph and deterministic graph/evidence grading helpers."""
from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Iterable

GRAPH_POLICY_VERSION = "repository-relationship-graph.v2"
GRAPH_SAMPLE_POLICY_VERSION = "repository-relationship-sample.v1"
GRAPH_MIN_SAMPLED_CONCEPTS = 7
GRAPH_MIN_CONCEPT_COVERAGE_PERCENT = 70.0
EVIDENCE_ROLE_POLICY_VERSION = "repository-evidence-roles.v2"

# Canonical graph nodes are conceptual authorities, not source file names.  The grader emits
# assertions using this vocabulary so semantically equivalent prose maps to one stable graph.
EXPECTED_EDGES: tuple[dict[str, Any], ...] = (
    {"subject":"PlanResolver","relation":"owns","object":"execution resolution","critical":True,"concept":"C002-plan-goon-compilation"},
    {"subject":"Executor","relation":"consumes","object":"resolved execution","critical":True,"concept":"C003-execution-safety"},
    {"subject":"API","relation":"must_not_own","object":"execution semantics","critical":True,"concept":"C001-repository-ownership"},
    {"subject":"Frontend","relation":"must_not_own","object":"backend contract facts","critical":True,"concept":"C008-frontend-authority-concurrency"},
    {"subject":"CertificationSetup","relation":"must_precede","object":"CertificationExecution","critical":True,"concept":"C011-startup-certification"},
    {"subject":"DependencyResolver","relation":"owns","object":"resolved dependency graph","critical":True,"concept":"C003-execution-safety"},
    {"subject":"OhGoon","relation":"consumes","object":"native dependency resolution evidence","critical":False,"concept":"C009-evidence-observability"},
    {"subject":"AttemptWorkspaceAuthority","relation":"owns","object":"attempt private paths","critical":True,"concept":"C007-isolation-tool-authority"},
    {"subject":"CommandRunner","relation":"owns","object":"managed process lifecycle","critical":True,"concept":"C007-isolation-tool-authority"},
    {"subject":"SealedAttemptOutputs","relation":"constrains","object":"output publication","critical":True,"concept":"C009-evidence-observability"},
    {"subject":"RunExecutionSupervisor","relation":"observes","object":"admitted database identity","critical":True,"concept":"C006-run-identity-lifecycle"},
    {"subject":"GameTape","relation":"consumes","object":"durable historical evidence","critical":False,"concept":"C009-evidence-observability"},
    {"subject":"Plan","relation":"must_not_own","object":"Step execution","critical":True,"concept":"C002-plan-goon-compilation"},
    {"subject":"AtomicGoon","relation":"owns","object":"Steps","critical":True,"concept":"C002-plan-goon-compilation"},
    {"subject":"CompositeGoon","relation":"consumes","object":"Goons","critical":False,"concept":"C002-plan-goon-compilation"},
    {"subject":"Hashmarks","relation":"owns","object":"filesystem content identity","critical":True,"concept":"C009-evidence-observability"},
    {"subject":"OhGoon","relation":"owns","object":"filesystem security policy","critical":True,"concept":"C010-cross-repository-security"},
)

FORBIDDEN_EDGES: tuple[dict[str, str], ...] = (
    {"subject":"API","relation":"owns","object":"execution semantics"},
    {"subject":"Frontend","relation":"owns","object":"backend contract facts"},
    {"subject":"Plan","relation":"owns","object":"Step execution"},
    {"subject":"OhGoon","relation":"owns","object":"resolved dependency graph"},
    {"subject":"Hashmarks","relation":"owns","object":"filesystem security policy"},
)


def _key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (str(edge["subject"]), str(edge["relation"]), str(edge["object"]))


def graph_spec() -> dict[str, Any]:
    return {
        "policy_version": GRAPH_POLICY_VERSION,
        "expected_edges": [dict(edge) for edge in EXPECTED_EDGES],
        "forbidden_edges": [dict(edge) for edge in FORBIDDEN_EDGES],
    }


def graph_manifest() -> dict[str, Any]:
    concepts = sorted({str(edge["concept"]) for edge in EXPECTED_EDGES})
    return {
        "policy_version": GRAPH_POLICY_VERSION,
        "sample_policy_version": GRAPH_SAMPLE_POLICY_VERSION,
        "nodes": sorted({value for edge in (*EXPECTED_EDGES, *FORBIDDEN_EDGES) for value in (edge["subject"], edge["object"])}),
        "relations": sorted({edge["relation"] for edge in (*EXPECTED_EDGES, *FORBIDDEN_EDGES)}),
        "concepts": concepts,
        "expected_edge_count": len(EXPECTED_EDGES),
        "critical_expected_edge_count": sum(bool(edge.get("critical")) for edge in EXPECTED_EDGES),
        "forbidden_edge_count": len(FORBIDDEN_EDGES),
        "minimum_sampled_concepts": GRAPH_MIN_SAMPLED_CONCEPTS,
        "minimum_concept_coverage_percent": GRAPH_MIN_CONCEPT_COVERAGE_PERCENT,
    }


def _percentage(numerator: int, denominator: int) -> float:
    return 100.0 if denominator == 0 else round(100.0 * numerator / denominator, 1)


def score_assertion_graph(assertions: Iterable[dict[str, str]], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = graph_spec() if spec is None else spec
    if spec.get("policy_version") != GRAPH_POLICY_VERSION:
        raise ValueError("repository relationship graph policy version mismatch")
    expected_rows = spec.get("expected_edges")
    forbidden_rows = spec.get("forbidden_edges")
    if not isinstance(expected_rows, list) or not isinstance(forbidden_rows, list):
        raise ValueError("repository relationship graph spec is incomplete")
    assertion_rows = tuple(assertions)
    affirmed = {_key(a) for a in assertion_rows if a.get("polarity") == "AFFIRMED"}
    denied = {_key(a) for a in assertion_rows if a.get("polarity") == "DENIED"}
    expected = {_key(e): bool(e.get("critical")) for e in expected_rows}
    edge_concepts = {_key(e): str(e.get("concept", "UNCLASSIFIED")) for e in expected_rows}
    forbidden = {_key(e) for e in forbidden_rows}
    matched = sorted(key for key in expected if key in affirmed)
    missing = sorted(key for key in expected if key not in affirmed)
    missing_critical = sorted(key for key in missing if expected[key])
    forbidden_affirmed = sorted(key for key in forbidden if key in affirmed)
    forbidden_denied = sorted(key for key in forbidden if key in denied)
    precision = _percentage(len([k for k in affirmed if k in expected]), len(affirmed))
    recall = _percentage(len(matched), len(expected))
    critical_expected = [k for k, critical in expected.items() if critical]
    critical_recall = _percentage(len([k for k in critical_expected if k in affirmed]), len(critical_expected))

    concept_names = sorted(set(edge_concepts.values()))
    concept_scores: dict[str, dict[str, Any]] = {}
    sampled_concepts = 0
    for concept in concept_names:
        concept_expected = [key for key, value in edge_concepts.items() if value == concept]
        concept_expected_set = set(concept_expected)
        concept_critical = [key for key in concept_expected if expected[key]]
        related_assertions = [key for key in affirmed | denied if key in concept_expected_set or key in forbidden]
        sampled = bool(related_assertions)
        if sampled:
            sampled_concepts += 1
        concept_matched = [key for key in concept_expected if key in affirmed]
        concept_missing = [key for key in concept_expected if key not in affirmed]
        concept_missing_critical = [key for key in concept_missing if expected[key]]
        concept_forbidden = [key for key in forbidden_affirmed if key[0] in {edge[0] for edge in concept_expected}]
        concept_affirmed = [key for key in affirmed if key in concept_expected_set]
        concept_scores[concept] = {
            "expected_edges": len(concept_expected),
            "critical_expected_edges": len(concept_critical),
            "assertion_count": len(related_assertions),
            "sampled": sampled,
            "precision_percent": _percentage(len(concept_matched), len(concept_affirmed)),
            "recall_percent": _percentage(len(concept_matched), len(concept_expected)),
            "critical_recall_percent": _percentage(len([key for key in concept_critical if key in affirmed]), len(concept_critical)),
            "missing_edges": [list(key) for key in concept_missing],
            "missing_critical_edges": [list(key) for key in concept_missing_critical],
            "forbidden_affirmed_edges": [list(key) for key in concept_forbidden],
            "status": "NOT_ENOUGH_EVIDENCE" if not sampled else "FAIL" if concept_missing_critical or concept_forbidden else "PASS",
        }

    concept_coverage = _percentage(sampled_concepts, len(concept_names))
    sample_coverage_ok = sampled_concepts >= GRAPH_MIN_SAMPLED_CONCEPTS and concept_coverage >= GRAPH_MIN_CONCEPT_COVERAGE_PERCENT
    return {
        "policy_version": GRAPH_POLICY_VERSION,
        "sample_policy_version": GRAPH_SAMPLE_POLICY_VERSION,
        "precision_percent": precision,
        "recall_percent": recall,
        "critical_recall_percent": critical_recall,
        "matched_edges": [list(k) for k in matched],
        "missing_edges": [list(k) for k in missing],
        "missing_critical_edges": [list(k) for k in missing_critical],
        "forbidden_affirmed_edges": [list(k) for k in forbidden_affirmed],
        "forbidden_denied_edges": [list(k) for k in forbidden_denied],
        "concept_scores": concept_scores,
        "sample_coverage": {
            "sampled_concepts": sampled_concepts,
            "available_concepts": len(concept_names),
            "coverage_percent": concept_coverage,
            "minimum_sampled_concepts": GRAPH_MIN_SAMPLED_CONCEPTS,
            "minimum_coverage_percent": GRAPH_MIN_CONCEPT_COVERAGE_PERCENT,
            "status": "ENOUGH" if sample_coverage_ok else "NOT_ENOUGH_EVIDENCE",
        },
        "status": "NOT_ENOUGH_EVIDENCE" if not sample_coverage_ok else "FAIL" if missing_critical or forbidden_affirmed else "PASS",
    }


def evidence_authority_class(reference: str) -> str:
    path = PurePosixPath(reference.split(":", 1)[0])
    text = path.as_posix()
    if text.startswith("generated/"):
        return "A0"
    if "/tests/" in f"/{text}" or text.startswith("tests/") or text.startswith("scripts/tests/"):
        return "A2"
    if text.endswith((".yml", ".yaml", ".json")) and ("schema" in text or "contract" in text or text.startswith("generated/")):
        return "A3"
    if text.endswith("README.md") or text == "AGENTS.md" or "/README.md" in text:
        return "A4"
    if text.startswith("docs/"):
        return "A5"
    if text.startswith(("backend/src/", "frontend/src/")):
        return "A1"
    return "A6"



_AUTHORITY_RANK = {f"A{i}": i for i in range(7)}


def evidence_authority_assessment(
    references: Iterable[str], minimum_authority: str
) -> dict[str, Any]:
    """Assess whether candidate evidence reaches the frozen authority floor.

    Authority is derived mechanically from repository paths.  A grader may still judge
    relevance/support, but it cannot promote lower-authority evidence into governing evidence.
    """

    if minimum_authority not in _AUTHORITY_RANK:
        raise ValueError(f"unknown minimum evidence authority: {minimum_authority}")
    rows = [(reference, evidence_authority_class(reference)) for reference in references]
    qualifying = [
        (reference, authority)
        for reference, authority in rows
        if _AUTHORITY_RANK[authority] <= _AUTHORITY_RANK[minimum_authority]
    ]
    nonqualifying = [
        (reference, authority)
        for reference, authority in rows
        if _AUTHORITY_RANK[authority] > _AUTHORITY_RANK[minimum_authority]
    ]
    strongest = min(
        (authority for _reference, authority in rows),
        key=lambda authority: _AUTHORITY_RANK[authority],
        default=None,
    )
    return {
        "policy_version": EVIDENCE_ROLE_POLICY_VERSION,
        "minimum_authority": minimum_authority,
        "meets_minimum_authority": bool(qualifying),
        "strongest_authority": strongest,
        "qualifying_references": [
            {"reference": reference, "authority": authority}
            for reference, authority in qualifying
        ],
        "nonqualifying_references": [
            {"reference": reference, "authority": authority}
            for reference, authority in nonqualifying
        ],
    }

def evidence_expectation_roles(references: Iterable[str], minimum_authority: str) -> dict[str, Any]:
    if minimum_authority not in _AUTHORITY_RANK:
        raise ValueError(f"unknown minimum evidence authority: {minimum_authority}")
    rows = [(reference, evidence_authority_class(reference)) for reference in references]
    strongest_rank = min((_AUTHORITY_RANK[authority] for _reference, authority in rows), default=None)
    canonical_minimum = None if strongest_rank is None else f"A{strongest_rank}"
    required = [
        (reference, authority)
        for reference, authority in rows
        if _AUTHORITY_RANK[authority] == strongest_rank
    ]
    alternatives = [
        (reference, authority)
        for reference, authority in rows
        if strongest_rank is not None
        and _AUTHORITY_RANK[authority] > strongest_rank
        and _AUTHORITY_RANK[authority] <= _AUTHORITY_RANK[minimum_authority]
    ]
    supplementary = [
        (reference, authority)
        for reference, authority in rows
        if _AUTHORITY_RANK[authority] > _AUTHORITY_RANK[minimum_authority]
    ]
    declared_minimum_met = bool(
        strongest_rank is not None and strongest_rank <= _AUTHORITY_RANK[minimum_authority]
    )
    return {
        "policy_version": EVIDENCE_ROLE_POLICY_VERSION,
        "minimum_authority": minimum_authority,
        "canonical_minimum_authority": canonical_minimum,
        "declared_minimum_met": declared_minimum_met,
        "required_authority": [{"reference": ref, "authority": auth} for ref, auth in required],
        "sufficient_alternatives": [{"reference": ref, "authority": auth} for ref, auth in alternatives],
        "supplementary": [{"reference": ref, "authority": auth} for ref, auth in supplementary],
        "independent_authority_classes_available": len(
            {
                authority
                for _reference, authority in rows
                if _AUTHORITY_RANK[authority] <= _AUTHORITY_RANK[minimum_authority]
            }
        ),
    }

def evidence_profile(references: Iterable[str]) -> dict[str, Any]:
    classes = [evidence_authority_class(reference) for reference in references]
    counts = Counter(classes)
    return {
        "policy_version": EVIDENCE_ROLE_POLICY_VERSION,
        "authority_classes": sorted(counts),
        "authority_class_counts": dict(sorted(counts.items())),
        "independent_authority_classes": len(counts),
        "strongest_authority": min(classes, default=None),
    }
