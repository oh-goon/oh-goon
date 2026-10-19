from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCORECARD = ROOT / "docs/development/EXECUTION_SCALABILITY_SCORECARD.yml"

EXPECTED = {
    "workflow_count",
    "duplicate_execution_owner_violations",
    "architecture_hard_violations",
    "queue_admission_latency",
    "focused_validation_duration",
    "certification_duration",
    "shard_duration_distribution",
    "timeout_refinement_counts",
    "retry_resume_recovery_counts",
    "process_tree_cleanup_failures",
    "materialization_bytes_time",
    "cache_reuse_vs_invalidation",
    "proof_evidence_reuse_rate",
    "generated_contract_drift_incidents",
    "artifact_replay_mismatch_incidents",
}


def _load() -> dict[str, Any]:
    payload = yaml.safe_load(SCORECARD.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_phase25_scorecard_covers_only_required_execution_metrics() -> None:
    data = _load()
    assert data["scope"] == "execution-domain-only"
    assert data["policy"] == {
        "diagnostic_only": True,
        "generic_metrics_store": "forbidden",
        "frontend_metric_authority": "forbidden",
        "hashmarks_agent_economics": "forbidden",
        "missing_data": "unknown-not-zero",
    }
    metrics = data["metrics"]
    assert set(metrics) == EXPECTED
    for metric in metrics.values():
        assert metric["unit"]
        assert metric["source"]
        assert metric["owner"]
        assert metric["availability"] in {"direct", "derived"}
        assert metric["aggregation"]


def test_scorecard_does_not_create_agent_economics_or_generic_metric_ownership() -> (
    None
):
    text = SCORECARD.read_text(encoding="utf-8").lower()
    for forbidden in (
        "token_count",
        "model_cost",
        "prompt_tokens",
        "completion_tokens",
        "genericmetric",
        "genericobservation",
    ):
        assert forbidden not in text
