from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from oh_goon.repository_tooling import repository_understanding_lifecycle as life

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
DIGEST_A = "a" * 64


def _row(qid: str, state: str = "ACTIVE", *, member: bool = True) -> dict[str, object]:
    projection = life._state_projection(state)
    return {
        "id": qid,
        "state": state,
        "introduced_source_version": "PRE_LIFECYCLE",
        "state_since_source_version": "1.0.0",
        **projection,
        "qualification_bank_member": member,
        "activation_basis": "LEGACY_ACTIVE" if state == "ACTIVE" else None,
    }


def _lifecycle(*rows: dict[str, object]) -> dict[str, object]:
    base = {
        "schema_version": life.QUESTION_LIFECYCLE_SCHEMA_VERSION,
        "policy_version": life.QUESTION_LIFECYCLE_POLICY_VERSION,
        "source_version": "1.0.0",
        "questions": list(rows),
    }
    return {**base, "lifecycle_identity": "sha256:" + life._canonical_sha256(base)}


def test_load_lifecycle_validates_schema_state_projection_membership_and_order(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.yml"
    base = {
        "schema_version": life.QUESTION_LIFECYCLE_SCHEMA_VERSION,
        "policy_version": life.QUESTION_LIFECYCLE_POLICY_VERSION,
        "source_version": "1.0.0",
        "questions": [_row("Q1")],
    }
    path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    loaded = life.load_question_lifecycle(path, source_version="1.0.0", question_ids=("Q1",))
    assert loaded["questions"][0]["state"] == "ACTIVE"
    mutations = [
        None,
        {**base, "schema_version": 99},
        {**base, "policy_version": "wrong"},
        {**base, "source_version": "2.0.0"},
        {**base, "questions": "bad"},
        {**base, "questions": ["bad"]},
        {**base, "questions": [{**_row("Q1"), "id": 1}]},
        {**base, "questions": [{**_row("Q1"), "state": "BAD"}]},
        {**base, "questions": [{**_row("Q1"), "score_bearing": False}]},
        {**base, "questions": [{**_row("Q1"), "activation_basis": "BAD"}]},
        {**base, "questions": [{**_row("Q1", "SHADOW", member=False), "activation_basis": "LEGACY_ACTIVE"}]},
        {**base, "questions": [{**_row("Q1"), "qualification_bank_member": "yes"}]},
        {**base, "questions": [{**_row("Q1"), "qualification_bank_member": False}]},
    ]
    for payload in mutations:
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError):
            life.load_question_lifecycle(path, source_version="1.0.0", question_ids=("Q1",))
    path.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="IDs/order"):
        life.load_question_lifecycle(path, source_version="1.0.0", question_ids=("Q2",))


def test_lifecycle_manifest_distinguishes_ready_degraded_blocked_and_active_bank() -> None:
    ready = _lifecycle(_row("Q1"), _row("Q2"))
    assert life.question_lifecycle_manifest(ready)["qualification_status"] == "READY"
    degraded = _lifecycle(_row("Q1"), _row("Q2", "REVIEW_REQUIRED"))
    assert life.question_lifecycle_manifest(degraded)["qualification_status"] == "DEGRADED"
    blocked = life.question_lifecycle_manifest(degraded, critical_question_ids=("Q2",))
    assert blocked["qualification_status"] == "BLOCKED"
    with pytest.raises(ValueError, match="non-ACTIVE"):
        life.assert_current_score_bank_active(degraded, critical_question_ids=("Q2",))
    assert life.assert_current_score_bank_active(ready)["score_bearing_count"] == 2
    assert life._median([]) is None and life._rate([]) is None
    assert life._median([1.0, 3.0]) == 2.0 and life._rate([True, False]) == 50.0


def _human_payload() -> dict[str, object]:
    return {
        "artifact_type": "repository-understanding-human-baseline-observations",
        "schema_version": life.HUMAN_BASELINE_OBSERVATION_SCHEMA_VERSION,
        "source_version": "1.0.0",
        "repository_identity": SHA_A,
        "exam_snapshot_sha256": DIGEST_A,
        "reviewers": [
            {
                "reviewer_id": "r1",
                "expertise": "maintainer",
                "questions": [{"id": "Q1", "outcome": "SUCCESS", "reference_assessment": "AGREE", "time_to_evidence_seconds": 1.0}],
            },
            {
                "reviewer_id": "r2",
                "expertise": "reviewer",
                "questions": [{"id": "Q1", "outcome": "PARTIAL", "reference_assessment": "SUSPECT", "time_to_evidence_seconds": 3.0}],
            },
        ],
    }


def _human_report(tmp_path: Path, payload: dict[str, object] | None = None) -> dict[str, object]:
    path = tmp_path / "human.json"; path.write_text(json.dumps(payload or _human_payload()), encoding="utf-8")
    return life.human_baseline_report(path, question_ids=("Q1", "Q2"), source_version="1.0.0", repository_identity=SHA_A, exam_snapshot_sha256=DIGEST_A)


def test_human_baseline_reports_disagreement_and_rejects_malformed_observations(tmp_path: Path) -> None:
    report = _human_report(tmp_path)
    assert report["maturity"] == "MULTI_REVIEWER"
    assert report["questions"]["Q1"]["reviewer_count"] == 2
    assert report["questions"]["Q1"]["reference_disagreement"] is True
    assert report["questions"]["Q2"]["success_rate_percent"] is None
    base = _human_payload()
    mutations = [
        None,
        {**base, "artifact_type": "wrong"},
        {**base, "schema_version": 99},
        {**base, "source_version": "wrong"},
        {**base, "repository_identity": SHA_B},
        {**base, "reviewers": []},
        {**base, "reviewers": ["bad"]},
    ]
    for payload in mutations:
        path = tmp_path / "bad.json"; path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            life.human_baseline_report(path, question_ids=("Q1",), source_version="1.0.0", repository_identity=SHA_A, exam_snapshot_sha256=DIGEST_A)
    for mutate in (
        lambda p: p["reviewers"][0].update(reviewer_id=""),
        lambda p: p["reviewers"][1].update(reviewer_id="r1"),
        lambda p: p["reviewers"][0].update(expertise=""),
        lambda p: p["reviewers"][0].update(questions="bad"),
        lambda p: p["reviewers"][0]["questions"].append("bad"),
        lambda p: p["reviewers"][0]["questions"][0].update(id="QX"),
        lambda p: p["reviewers"][0]["questions"].append(copy.deepcopy(p["reviewers"][0]["questions"][0])),
        lambda p: p["reviewers"][0]["questions"][0].update(outcome="BAD"),
        lambda p: p["reviewers"][0]["questions"][0].update(reference_assessment="BAD"),
        lambda p: p["reviewers"][0]["questions"][0].update(time_to_evidence_seconds=-1),
    ):
        payload = copy.deepcopy(base); mutate(payload)
        path = tmp_path / "bad2.json"; path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            life.human_baseline_report(path, question_ids=("Q1",), source_version="1.0.0", repository_identity=SHA_A, exam_snapshot_sha256=DIGEST_A)


def _write_request(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "transition.json"; path.write_text(json.dumps(payload), encoding="utf-8"); return path


def _request(lifecycle: dict[str, object], *, qid: str, from_state: str, to_state: str, **extra: object) -> dict[str, object]:
    return {
        "artifact_type": "repository-question-lifecycle-transition-request",
        "schema_version": life.QUESTION_TRANSITION_SCHEMA_VERSION,
        "policy_version": life.QUESTION_LIFECYCLE_POLICY_VERSION,
        "source_version": "1.0.0",
        "lifecycle_identity": lifecycle["lifecycle_identity"],
        "question_id": qid,
        "from_state": from_state,
        "to_state": to_state,
        **extra,
    }


def test_transition_authority_covers_simple_transitions_and_rejections(tmp_path: Path) -> None:
    audit = {"questions": [{"id": "Q1", "health_status": "VALID"}]}
    for state, target, extra, expected in [
        ("DRAFT", "SHADOW", {"author_review_identity": SHA_A}, "author_review_identity"),
        ("SHADOW", "CALIBRATING", {"author_review_identity": SHA_A, "shadow_observation_identity": SHA_B}, "shadow_observation_identity"),
        ("ACTIVE", "RETIRED", {"retirement_review_identity": SHA_A, "reason": "obsolete"}, "reason"),
    ]:
        lifecycle = _lifecycle(_row("Q1", state, member=state == "ACTIVE"))
        result = life.authorize_question_transition(_write_request(tmp_path, _request(lifecycle, qid="Q1", from_state=state, to_state=target, **extra)), lifecycle=lifecycle, question_audit=audit)
        assert result["authorized"] is True and expected in result["evidence"]
    lifecycle = _lifecycle(_row("Q1", "DRAFT", member=False))
    base = _request(lifecycle, qid="Q1", from_state="DRAFT", to_state="SHADOW", author_review_identity=SHA_A)
    for mutation in (
        {**base, "artifact_type": "bad"}, {**base, "schema_version": 99}, {**base, "policy_version": "bad"},
        {**base, "source_version": "bad"}, {**base, "lifecycle_identity": SHA_B}, {**base, "question_id": "QX"},
        {**base, "from_state": "ACTIVE"}, {**base, "to_state": "ACTIVE"}, {**base, "author_review_identity": "bad"},
    ):
        with pytest.raises(ValueError):
            life.authorize_question_transition(_write_request(tmp_path, mutation), lifecycle=lifecycle, question_audit=audit)


def _bound_artifact(payload: dict[str, object], field: str) -> dict[str, object]:
    return {**payload, field: "sha256:" + life._canonical_sha256(payload)}


def test_active_promotion_requires_valid_calibration_humans_review_and_adjudication(tmp_path: Path) -> None:
    lifecycle = _lifecycle(_row("Q1", "CALIBRATING", member=False))
    audit = {"questions": [{"id": "Q1", "health_status": "VALID"}]}
    cal = _bound_artifact({"source_version": "1.0.0", "questions": {"Q1": {"observation_count": 3}}}, "report_identity")
    human = _bound_artifact({"source_version": "1.0.0", "questions": {"Q1": {"reviewer_count": 2, "reference_disagreement": True}}}, "human_baseline_identity")
    req = _request(lifecycle, qid="Q1", from_state="CALIBRATING", to_state="ACTIVE", calibration_report_identity=cal["report_identity"], human_baseline_identity=human["human_baseline_identity"], promotion_review_identity=SHA_A, adjudication_identity=SHA_B)
    result = life.authorize_question_transition(_write_request(tmp_path, req), lifecycle=lifecycle, question_audit=audit, calibration_report=cal, human_baseline=human)
    assert result["evidence"]["adjudication_identity"] == SHA_B
    bad_audit = {"questions": [{"id": "Q1", "health_status": "STALE"}]}
    with pytest.raises(ValueError, match="health"):
        life.authorize_question_transition(_write_request(tmp_path, req), lifecycle=lifecycle, question_audit=bad_audit, calibration_report=cal, human_baseline=human)
    with pytest.raises(ValueError, match="requires calibration"):
        life.authorize_question_transition(_write_request(tmp_path, req), lifecycle=lifecycle, question_audit=audit)
    stale_cal = _bound_artifact({"source_version": "0.9.0", "questions": {"Q1": {"observation_count": 3}}}, "report_identity")
    with pytest.raises(ValueError, match="source_version"):
        life.authorize_question_transition(_write_request(tmp_path, req), lifecycle=lifecycle, question_audit=audit, calibration_report=stale_cal, human_baseline=human)
    no_obs = _bound_artifact({"source_version": "1.0.0", "questions": {"Q1": {"observation_count": 0}}}, "report_identity")
    req_no_obs = {**req, "calibration_report_identity": no_obs["report_identity"]}
    with pytest.raises(ValueError, match="no empirical"):
        life.authorize_question_transition(_write_request(tmp_path, req_no_obs), lifecycle=lifecycle, question_audit=audit, calibration_report=no_obs, human_baseline=human)
    one_human = _bound_artifact({"source_version": "1.0.0", "questions": {"Q1": {"reviewer_count": 1, "reference_disagreement": False}}}, "human_baseline_identity")
    req_one = {**req, "human_baseline_identity": one_human["human_baseline_identity"]}
    with pytest.raises(ValueError, match="two independent"):
        life.authorize_question_transition(_write_request(tmp_path, req_one), lifecycle=lifecycle, question_audit=audit, calibration_report=cal, human_baseline=one_human)
