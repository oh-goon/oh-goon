from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from oh_goon.repository_tooling import repository_understanding_capability as capability
from oh_goon.repository_tooling.repository_understanding_integrity import (
    ADVERSARIAL_POLICY_VERSION,
    ADVERSARIAL_RESULT_SCHEMA_VERSION,
    BENCHMARK_CONTROL_REQUIREMENTS,
    BENCHMARK_INTEGRITY_POLICY_VERSION,
    BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION,
)
from oh_goon.repository_tooling.repository_understanding_stability import (
    STABILITY_PASSES_REQUIRED,
    STABILITY_POLICY_VERSION,
    STABILITY_RUNS_REQUIRED,
)

type CertificateEvidence = tuple[
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]


def _sha(value: str) -> str:
    return "sha256:" + value * 64


def _sign(payload: dict[str, object], field: str) -> dict[str, object]:
    signed = deepcopy(payload)
    signed.pop(field, None)
    signed[field] = "sha256:" + capability._canonical_sha256(signed)
    return signed


def _certificate(*, status: str = "AUTHORIZED") -> dict[str, object]:
    policy = capability.CAPABILITY_TIERS["release-author.v1"]
    authorized = status == "AUTHORIZED"
    payload: dict[str, object] = {
        "artifact_type": "repository-understanding-capability-certificate",
        "schema_version": capability.CAPABILITY_CERTIFICATE_SCHEMA_VERSION,
        "capability_policy_version": capability.CAPABILITY_POLICY_VERSION,
        "qualification_profile": "release-author.v1",
        "target_tier": policy["tier"],
        "authority_class": policy["authority_class"],
        "certificate_status": status,
        "granted_tier": policy["tier"] if authorized else None,
        "authorization": (
            dict(policy["authorization"])
            if authorized
            else {action: False for action in capability.CAPABILITY_ACTIONS}
        ),
        "runs_required": STABILITY_RUNS_REQUIRED,
        "passes_required": STABILITY_PASSES_REQUIRED,
        "stability_policy_version": STABILITY_POLICY_VERSION,
        "benchmark_integrity_policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "candidate_run_identities": ["run-1", "run-2", "run-3"],
        "run_certificate_identities": [_sha("1"), _sha("2"), _sha("3")],
        "benchmark_integrity_result_identities": [_sha("4"), _sha("5"), _sha("6")],
        "challenge_instance_identities": [_sha("7"), _sha("8"), _sha("9")],
        "adversarial_result_identities": [_sha("a"), _sha("b"), _sha("c")],
        "repository_identity": _sha("d"),
        "exam_snapshot_sha256": "e" * 64,
        "candidate_configuration_identity": _sha("f"),
        "benchmark_registry_identity": _sha("0"),
        "issued_at_utc": "2026-09-15T00:00:00Z",
        "freshness": {
            "time_expiry": None,
            "time_expiry_policy": "NOT_CALIBRATED_V1",
            "repository_identity_must_match": True,
            "candidate_configuration_identity_must_match": True,
            "note": "v1 is exact-source and exact-candidate-configuration bound; no arbitrary time TTL is invented",
        },
        "scope_model": "repository-wide-action-scopes.v1",
        "baseline_without_certificate": "READ_ONLY",
        "enforcement_status": "EVIDENCE_ONLY",
        "gate_failures": [] if authorized else ["stable_qualification_required"],
        "passes": STABILITY_PASSES_REQUIRED if authorized else 0,
        "comparable_runs": STABILITY_RUNS_REQUIRED if authorized else 0,
        "adversarial_qualified_runs": STABILITY_RUNS_REQUIRED if authorized else 0,
        "stability_gate_failures": [],
        "critical_concept_oscillations": [],
    }
    return _sign(payload, "capability_certificate_identity")


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _integrity() -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_type": "repository-understanding-benchmark-integrity-result",
        "schema_version": BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION,
        "policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "required_controls": deepcopy(BENCHMARK_CONTROL_REQUIREMENTS),
        "observed_controls": deepcopy(BENCHMARK_CONTROL_REQUIREMENTS),
        "control_failures": [],
        "comparable": True,
        "comparison_status": "COMPARABLE",
    }
    return _sign(payload, "integrity_result_identity")


def _adversarial() -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_type": "repository-understanding-adversarial-result",
        "schema_version": ADVERSARIAL_RESULT_SCHEMA_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "benchmark_integrity_policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "critical_adversarial_failures": [],
        "adversarial_gate_failures": [],
        "qualified": True,
    }
    return _sign(payload, "adversarial_result_identity")


def test_identity_digest_and_issue_time_validation_fail_closed() -> None:
    assert capability._identity(_sha("a"), label="value") == _sha("a")
    assert capability._digest("b" * 64, label="digest") == "b" * 64
    assert capability._issued_at("2026-09-15T00:00:00Z") == "2026-09-15T00:00:00Z"
    assert capability._issued_at(None).endswith("Z")

    for value in (None, "sha256:short", "sha256:" + "g" * 64):
        with pytest.raises(ValueError, match="sha256 identity"):
            capability._identity(value, label="value")
    for value in (None, "short", "g" * 64):
        with pytest.raises(ValueError, match="64-character sha256 digest"):
            capability._digest(value, label="digest")
    for value in ("", "not-a-time", "2026-09-15T00:00:00", "2026-09-15T02:00:00+02:00"):
        with pytest.raises(ValueError, match="issued_at_utc"):
            capability._issued_at(value)


def test_json_integrity_and_adversarial_loaders_reject_inconsistent_evidence(
    tmp_path: Path,
) -> None:
    assert (
        capability._load_integrity(_write(tmp_path / "integrity.json", _integrity()))[
            "comparable"
        ]
        is True
    )
    assert (
        capability._load_adversarial(
            _write(tmp_path / "adversarial.json", _adversarial())
        )["qualified"]
        is True
    )
    with pytest.raises(ValueError, match="must be a JSON object"):
        capability._load_json(_write(tmp_path / "list.json", []), label="input")

    mutations = [
        ("artifact_type", "wrong", "not a benchmark integrity result"),
        ("schema_version", -1, "schema mismatch"),
        ("policy_version", "wrong", "policy mismatch"),
        ("required_controls", {}, "required controls drifted"),
        ("observed_controls", [], "observed_controls must be an object"),
        ("control_failures", ["wrong"], "control_failures are inconsistent"),
        ("comparable", False, "comparable flag is inconsistent"),
        ("comparison_status", "WRONG", "comparison_status is inconsistent"),
    ]
    for index, (field, value, message) in enumerate(mutations):
        payload = _integrity()
        payload[field] = value
        payload = _sign(payload, "integrity_result_identity")
        with pytest.raises(ValueError, match=message):
            capability._load_integrity(
                _write(tmp_path / f"integrity-{index}.json", payload)
            )

    adversarial_mutations = [
        ("artifact_type", "wrong", "not an adversarial result"),
        ("schema_version", -1, "schema mismatch"),
        ("adversarial_policy_version", "wrong", "policy mismatch"),
        (
            "benchmark_integrity_policy_version",
            "wrong",
            "benchmark-integrity policy mismatch",
        ),
        ("critical_adversarial_failures", "bad", "failure fields must be lists"),
        (
            "adversarial_gate_failures",
            ["wrong"],
            "qualification fields are inconsistent",
        ),
    ]
    for index, (field, value, message) in enumerate(adversarial_mutations):
        payload = _adversarial()
        payload[field] = value
        payload = _sign(payload, "adversarial_result_identity")
        with pytest.raises(ValueError, match=message):
            capability._load_adversarial(
                _write(tmp_path / f"adversarial-{index}.json", payload)
            )


def test_concept_inventory_uses_only_included_questions_and_marks_critical_claims() -> (
    None
):
    concepts, critical = capability._concept_inventory(
        {
            "questions": [
                {"included": False, "concept_family": "ignored"},
                {
                    "included": True,
                    "concept_family": "routing",
                    "severity": "normal",
                    "claims": [],
                },
                {
                    "included": True,
                    "concept_family": "authority",
                    "severity": "normal",
                    "claims": [{"critical": True}],
                },
                {
                    "included": True,
                    "concept_family": "release",
                    "severity": "critical",
                    "claims": [],
                },
            ]
        }
    )
    assert concepts == ["authority", "release", "routing"]
    assert critical == ["authority", "release"]
    with pytest.raises(ValueError, match="missing question results"):
        capability._concept_inventory({})
    with pytest.raises(ValueError, match="invalid concept family"):
        capability._concept_inventory(
            {"questions": [{"included": True, "concept_family": ""}]}
        )


def test_validate_capability_certificate_accepts_authorized_and_denied_states() -> None:
    authorized = _certificate()
    denied = _certificate(status="DENIED")
    assert capability.validate_capability_certificate(authorized) == authorized
    assert capability.validate_capability_certificate(denied) == denied


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_type", "wrong", "artifact_type mismatch"),
        ("schema_version", -1, "schema mismatch"),
        ("capability_policy_version", "wrong", "policy mismatch"),
        ("qualification_profile", "unknown", "has no current mapping"),
        ("target_tier", "PLAN", "tier/profile mapping is inconsistent"),
        ("certificate_status", "MAYBE", "status is invalid"),
        ("authorization", {}, "authorization map is inconsistent"),
        ("runs_required", 2, "stability requirement is inconsistent"),
        ("stability_policy_version", "wrong", "stability policy mismatch"),
        (
            "benchmark_integrity_policy_version",
            "wrong",
            "benchmark-integrity policy mismatch",
        ),
        ("adversarial_policy_version", "wrong", "adversarial policy mismatch"),
        (
            "candidate_run_identities",
            ["run-1", "run-1", "run-3"],
            "three distinct candidate run identities",
        ),
        ("granted_tier", "PLAN", "inconsistent grant state"),
        ("passes", 2, "3/3 stability passes"),
        ("comparable_runs", 2, "3/3 comparable runs"),
        ("adversarial_qualified_runs", 2, "3/3 adversarial-qualified runs"),
        ("stability_gate_failures", ["unstable"], "unstable concept evidence"),
        ("repository_identity", "bad", "sha256 identity"),
        ("exam_snapshot_sha256", "bad", "64-character sha256 digest"),
        ("issued_at_utc", None, "issued_at_utc is required"),
        ("freshness", {}, "freshness contract is inconsistent"),
        ("scope_model", "wrong", "scope model mismatch"),
        (
            "baseline_without_certificate",
            "WRITE",
            "enforcement boundary is inconsistent",
        ),
    ],
)
def test_validate_capability_certificate_rejects_tampered_contract(
    field: str, value: object, message: str
) -> None:
    payload = _certificate()
    payload[field] = value
    payload = _sign(payload, "capability_certificate_identity")
    with pytest.raises(ValueError, match=message):
        capability.validate_capability_certificate(payload)


def test_validate_capability_certificate_rejects_self_identity_tamper() -> None:
    payload = _certificate()
    payload["capability_certificate_identity"] = _sha("1")
    with pytest.raises(ValueError, match="identity does not match artifact bytes"):
        capability.validate_capability_certificate(payload)


def test_capability_check_requires_known_action_and_binds_repository_and_candidate(
    tmp_path: Path,
) -> None:
    certificate = _certificate()
    path = _write(tmp_path / "certificate.json", certificate)
    accepted = capability.capability_check(
        path,
        repository_identity=cast(str, certificate["repository_identity"]),
        candidate_configuration_identity=cast(
            str, certificate["candidate_configuration_identity"]
        ),
        requested_capability="release_promotion",
    )
    assert accepted["authorized"] is True
    assert accepted["decision"] == "AUTHORIZED"
    assert accepted["failures"] == []

    rejected = capability.capability_check(
        path,
        repository_identity=_sha("1"),
        candidate_configuration_identity=_sha("2"),
        requested_capability="release_promotion",
    )
    assert rejected["authorized"] is False
    assert rejected["failures"] == [
        "repository_identity_mismatch",
        "candidate_configuration_identity_mismatch",
    ]
    with pytest.raises(ValueError, match="requested capability must be one of"):
        capability.capability_check(
            path,
            repository_identity=cast(str, certificate["repository_identity"]),
            candidate_configuration_identity=cast(
                str, certificate["candidate_configuration_identity"]
            ),
            requested_capability="root",
        )


def _certificate_stability(
    runs: list[str],
    repository_identity: str,
    snapshot_digest: str,
    configuration_identity: str,
) -> dict[str, object]:
    return {
        "qualification_profile": "release-author.v1",
        "source_version": "1267.0.883",
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": snapshot_digest,
        "candidate_configuration_identity": configuration_identity,
        "candidate_run_identities": list(runs),
        "qualified": True,
        "stability": "STABLE",
        "passes": STABILITY_PASSES_REQUIRED,
        "stability_certificate_identity": _sha("1"),
        "run_certificate_identities": [_sha("2"), _sha("3"), _sha("4")],
        "qa_schema_version": 1,
        "grader_schema_version": 1,
        "rubric_version": "rubric.v1",
        "scoring_policy_version": "scoring.v1",
        "qualification_policy_version": "qualification.v1",
        "qualification_rate_percent": 100.0,
        "median_composite_understanding": 99.0,
        "worst_critical_dimension": 95.0,
        "claim_agreement_rate_percent": 100.0,
        "concept_agreement_rate_percent": 100.0,
        "critical_concept_oscillations": [],
        "stability_gate_failures": [],
    }


def _certificate_results(runs: list[str]) -> list[dict[str, object]]:
    question_rows = [
        {
            "included": True,
            "concept_family": "authority",
            "severity": "critical",
            "claims": [{"critical": True}],
        },
        {
            "included": True,
            "concept_family": "routing",
            "severity": "normal",
            "claims": [],
        },
    ]
    return [{"questions": [dict(row) for row in question_rows]} for _run in runs]


def _certificate_integrity(
    runs: list[str],
    stability: dict[str, object],
    repository_identity: str,
    snapshot_digest: str,
    configuration_identity: str,
) -> list[dict[str, object]]:
    return [
        {
            "source_version": stability["source_version"],
            "repository_identity": repository_identity,
            "exam_snapshot_sha256": snapshot_digest,
            "candidate_configuration_identity": configuration_identity,
            "candidate_run_identity": run,
            "integrity_result_identity": _sha(str(index + 5)),
            "challenge_instance_identity": _sha(chr(ord("a") + index)),
            "benchmark_registry_identity": _sha("0"),
            "comparable": True,
        }
        for index, run in enumerate(runs)
    ]


def _certificate_inputs() -> CertificateEvidence:
    runs = ["run-1", "run-2", "run-3"]
    repository_identity = _sha("d")
    configuration_identity = _sha("f")
    snapshot_digest = "e" * 64
    stability = _certificate_stability(
        runs, repository_identity, snapshot_digest, configuration_identity
    )
    results = _certificate_results(runs)
    integrity = _certificate_integrity(
        runs, stability, repository_identity, snapshot_digest, configuration_identity
    )
    adversarial = [
        {
            "source_version": stability["source_version"],
            "repository_identity": repository_identity,
            "exam_snapshot_sha256": snapshot_digest,
            "challenge_instance_identity": integrity[index][
                "challenge_instance_identity"
            ],
            "benchmark_registry_identity": _sha("0"),
            "adversarial_result_identity": _sha(chr(ord("4") + index)),
            "qualified": True,
        }
        for index, _run in enumerate(runs)
    ]
    return stability, results, integrity, adversarial


def _issue_with_evidence(
    monkeypatch: pytest.MonkeyPatch, evidence: CertificateEvidence
) -> dict[str, object]:
    stability, results, integrity, adversarial = evidence
    monkeypatch.setattr(capability, "stability_report", lambda _paths: stability)
    monkeypatch.setattr(capability, "_result_payloads", lambda _paths: results)
    integrity_iter = iter(integrity)
    adversarial_iter = iter(adversarial)
    monkeypatch.setattr(
        capability, "_load_integrity", lambda _path: next(integrity_iter)
    )
    monkeypatch.setattr(
        capability, "_load_adversarial", lambda _path: next(adversarial_iter)
    )
    paths = [Path("one.json"), Path("two.json"), Path("three.json")]
    return capability.capability_certificate(
        paths,
        integrity_paths=paths,
        adversarial_paths=paths,
        issued_at_utc="2026-09-15T00:00:00Z",
    )


def test_capability_certificate_issues_identity_bound_authorized_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _issue_with_evidence(monkeypatch, _certificate_inputs())
    assert payload["certificate_status"] == "AUTHORIZED"
    assert payload["granted_tier"] == "CRITICAL"
    assert cast(dict[str, bool], payload["authorization"])["release_promotion"] is True
    assert payload["candidate_run_identities"] == ["run-1", "run-2", "run-3"]
    assert payload["comparable_runs"] == 3
    assert payload["adversarial_qualified_runs"] == 3
    assert payload["certified_concepts"] == ["authority", "routing"]
    assert payload["critical_concepts"] == ["authority"]
    assert capability.validate_capability_certificate(payload) == payload


def test_capability_certificate_denies_when_stability_or_hidden_challenge_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _certificate_inputs()
    stability, _results, integrity, adversarial = evidence
    stability["qualified"] = False
    stability["stability"] = "UNSTABLE"
    stability["passes"] = 2
    integrity[1]["comparable"] = False
    adversarial[2]["qualified"] = False

    payload = _issue_with_evidence(monkeypatch, evidence)

    assert payload["certificate_status"] == "DENIED"
    assert payload["granted_tier"] is None
    assert not any(cast(dict[str, bool], payload["authorization"]).values())
    assert payload["gate_failures"] == [
        "stable_qualification_required",
        "three_qualified_runs_required",
        "benchmark_not_comparable:run-2",
        "critical_adversarial_failure:run-3",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda s, r, i, a: s.update({"qualification_profile": "unknown"}),
            "no capability mapping",
        ),
        (lambda s, r, i, a: s.pop("source_version"), "missing source_version"),
        (
            lambda s, r, i, a: i[0].update({"repository_identity": _sha("1")}),
            "repository_identity does not match",
        ),
        (
            lambda s, r, i, a: a[0].update({"exam_snapshot_sha256": "1" * 64}),
            "exam_snapshot_sha256 does not match",
        ),
        (
            lambda s, r, i, a: s.pop("candidate_configuration_identity"),
            "missing candidate_configuration_identity",
        ),
        (
            lambda s, r, i, a: i[0].update(
                {"candidate_configuration_identity": _sha("1")}
            ),
            "candidate_configuration_identity does not match",
        ),
        (
            lambda s, r, i, a: s.update(
                {"candidate_run_identities": ["run-1", "run-1", "run-3"]}
            ),
            "three distinct candidate run identities",
        ),
        (
            lambda s, r, i, a: i[1].update({"candidate_run_identity": "run-1"}),
            "one unique integrity result per candidate run",
        ),
        (
            lambda s, r, i, a: i[2].update({"candidate_run_identity": "run-extra"}),
            "run identities do not exactly match",
        ),
        (
            lambda s, r, i, a: i[1].update(
                {"integrity_result_identity": i[0]["integrity_result_identity"]}
            ),
            "distinct benchmark integrity result identities",
        ),
        (
            lambda s, r, i, a: i[0].update({"challenge_instance_identity": None}),
            "one hidden challenge instance",
        ),
        (
            lambda s, r, i, a: i[1].update(
                {"challenge_instance_identity": i[0]["challenge_instance_identity"]}
            ),
            "distinct hidden challenge instances",
        ),
        (
            lambda s, r, i, a: a[1].update(
                {"challenge_instance_identity": a[0]["challenge_instance_identity"]}
            ),
            "one unique adversarial result per challenge instance",
        ),
        (
            lambda s, r, i, a: a[2].update({"challenge_instance_identity": _sha("9")}),
            "challenge identities do not exactly match",
        ),
        (
            lambda s, r, i, a: a[0].update({"benchmark_registry_identity": _sha("1")}),
            "one exact benchmark registry identity",
        ),
    ],
)
def test_capability_certificate_rejects_cross_run_evidence_drift(
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[
        [
            dict[str, object],
            list[dict[str, object]],
            list[dict[str, object]],
            list[dict[str, object]],
        ],
        object,
    ],
    message: str,
) -> None:
    evidence = _certificate_inputs()
    mutation(*evidence)
    with pytest.raises(ValueError, match=message):
        _issue_with_evidence(monkeypatch, evidence)


def test_capability_certificate_requires_exact_three_input_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [Path("one.json"), Path("two.json"), Path("three.json")]
    with pytest.raises(ValueError, match="exactly 3 scored runs"):
        capability.capability_certificate(
            paths[:2], integrity_paths=paths, adversarial_paths=paths
        )
    with pytest.raises(ValueError, match="exactly 3 benchmark integrity results"):
        capability.capability_certificate(
            paths, integrity_paths=paths[:2], adversarial_paths=paths
        )
    with pytest.raises(ValueError, match="exactly 3 adversarial results"):
        capability.capability_certificate(
            paths, integrity_paths=paths, adversarial_paths=paths[:2]
        )
