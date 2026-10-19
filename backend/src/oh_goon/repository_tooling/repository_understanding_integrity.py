"""Adversarial challenge and benchmark-integrity authority for Repository Understanding.

This module deliberately does not own semantic answer truth. Hidden paraphrase, pressure, canary,
and fresh-challenge prompts bind back to canonical Repository Understanding questions, so the
existing SECRET expected claims/evidence remain the sole semantic grading authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml

ADVERSARIAL_POLICY_VERSION = "repository-understanding-adversarial.v1"
BENCHMARK_INTEGRITY_POLICY_VERSION = "repository-understanding-benchmark-integrity.v2"
BENCHMARK_REGISTRY_SCHEMA_VERSION = 2
CHALLENGE_INSTANCE_SCHEMA_VERSION = 2
ADVERSARIAL_OBSERVATION_SCHEMA_VERSION = 1
ADVERSARIAL_RESULT_SCHEMA_VERSION = 1
BENCHMARK_EXECUTION_EVIDENCE_SCHEMA_VERSION = 1
BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION = 1

PROMPT_SOURCE_REFERENCE = re.compile(r"[A-Za-z0-9_./-]+:\d+(?:-\d+)?")
FORBIDDEN_PROMPT_MARKERS = ("docs/certification/answers", "expected_claims", "why_it_matters")
CHALLENGE_ROLES = ("PARAPHRASE", "CONTRADICTION_PRESSURE", "CANARY", "FRESH_SHADOW")
PARAPHRASE_OUTCOMES = ("CONSISTENT", "INCONSISTENT", "ABSTAINED")
PRESSURE_OUTCOMES = (
    "EVIDENCE_BACKED_RESISTANCE",
    "UNSUPPORTED_YIELD",
    "INCONSISTENT",
    "ABSTAINED",
)
FALSE_PREMISE_RESPONSES = ("REJECTED", "ACCEPTED", "NOT_ADDRESSED")
BENCHMARK_CONTROL_REQUIREMENTS = {
    "network_access": "DENIED",
    "prior_conversation_history": "DENIED",
    "prior_candidate_answers": "DENIED",
    "grader_secret_material": "DENIED",
    "candidate_workspace_mutation": "DENIED",
    "fresh_session": "REQUIRED",
    "candidate_configuration_frozen": "REQUIRED",
}


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _question_index(questions: Iterable[Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    rows = list(questions)
    by_id: dict[str, Any] = {}
    false_premises: list[str] = []
    for question in rows:
        qid = getattr(question, "id", None)
        if not isinstance(qid, str) or qid in by_id:
            raise ValueError("benchmark registry requires unique canonical question IDs")
        by_id[qid] = question
        if getattr(question, "type", None) == "FALSE_PREMISE":
            false_premises.append(qid)
    if not by_id:
        raise ValueError("benchmark registry requires canonical questions")
    return by_id, tuple(false_premises)


def _prompt(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} prompt must be a non-empty string")
    normalized = value.strip()
    if PROMPT_SOURCE_REFERENCE.search(normalized):
        raise ValueError(f"{label} prompt leaks a source line reference")
    if any(marker in normalized for marker in FORBIDDEN_PROMPT_MARKERS):
        raise ValueError(f"{label} prompt leaks grader-only material")
    return normalized


def _challenge_id(value: Any, *, label: str, seen: set[str]) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z][A-Z0-9-]{2,63}", value) is None:
        raise ValueError(f"{label} id must be an uppercase stable identifier")
    if value in seen:
        raise ValueError(f"duplicate benchmark challenge id: {value}")
    seen.add(value)
    return value


def _canonical_target(value: Any, *, label: str, questions: dict[str, Any]) -> str:
    if not isinstance(value, str) or value not in questions:
        raise ValueError(f"{label} references unknown canonical question: {value!r}")
    return value


def _introduced_source_version(value: Any, *, label: str, source_version: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d+\.\d+\.\d+", value) is None:
        raise ValueError(f"{label} introduced_source_version must be a canonical numeric version")
    introduced = tuple(int(part) for part in value.split("."))
    current = tuple(int(part) for part in source_version.split("."))
    if introduced > current:
        raise ValueError(f"{label} introduced_source_version cannot be in the future")
    return value


def load_benchmark_registry(
    path: Path,
    *,
    source_version: str,
    questions: Iterable[Any],
) -> dict[str, Any]:
    """Load SECRET benchmark variants without duplicating canonical answer truth."""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark integrity registry must be a mapping")
    if payload.get("schema_version") != BENCHMARK_REGISTRY_SCHEMA_VERSION:
        raise ValueError("benchmark integrity registry schema mismatch")
    if payload.get("policy_version") != BENCHMARK_INTEGRITY_POLICY_VERSION:
        raise ValueError("benchmark integrity registry policy mismatch")
    if payload.get("adversarial_policy_version") != ADVERSARIAL_POLICY_VERSION:
        raise ValueError("benchmark integrity adversarial policy mismatch")
    if payload.get("source_version") != source_version:
        raise ValueError("benchmark integrity registry source_version mismatch")

    question_by_id, false_premise_ids = _question_index(questions)
    seen_ids: set[str] = set()

    families_raw = payload.get("equivalence_families")
    if not isinstance(families_raw, list) or not families_raw:
        raise ValueError("benchmark integrity registry requires equivalence_families")
    families: list[dict[str, Any]] = []
    family_ids: set[str] = set()
    for family in families_raw:
        if not isinstance(family, dict):
            raise ValueError("equivalence family must be an object")
        family_id = family.get("id")
        if not isinstance(family_id, str) or re.fullmatch(r"EQ\d{3}", family_id) is None:
            raise ValueError("equivalence family id must use EQNNN")
        if family_id in family_ids:
            raise ValueError(f"duplicate equivalence family id: {family_id}")
        family_ids.add(family_id)
        qid = _canonical_target(
            family.get("question_id"), label=family_id, questions=question_by_id
        )
        variants_raw = family.get("variants")
        if not isinstance(variants_raw, list) or len(variants_raw) < 2:
            raise ValueError(f"{family_id} must contain at least two hidden paraphrase variants")
        variants: list[dict[str, str]] = []
        for variant in variants_raw:
            if not isinstance(variant, dict):
                raise ValueError(f"{family_id} variant must be an object")
            challenge_id = _challenge_id(variant.get("id"), label=family_id, seen=seen_ids)
            variants.append(
                {
                    "id": challenge_id,
                    "prompt": _prompt(variant.get("prompt"), label=challenge_id),
                }
            )
        families.append(
            {
                "id": family_id,
                "question_id": qid,
                "critical": getattr(question_by_id[qid], "severity", None) == "critical",
                "variants": variants,
            }
        )

    def normalize_rows(
        key: str,
        *,
        role: str,
        require_never_calibrated: bool = False,
        require_shadow: bool = False,
    ) -> list[dict[str, Any]]:
        raw = payload.get(key)
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"benchmark integrity registry requires {key}")
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"{key} entry must be an object")
            challenge_id = _challenge_id(item.get("id"), label=key, seen=seen_ids)
            qid = _canonical_target(
                item.get("question_id"), label=challenge_id, questions=question_by_id
            )
            row: dict[str, Any] = {
                "id": challenge_id,
                "question_id": qid,
                "prompt": _prompt(item.get("prompt"), label=challenge_id),
                "role": role,
                "critical": getattr(question_by_id[qid], "severity", None) == "critical",
            }
            if require_never_calibrated:
                if (
                    item.get("calibration_exposure") != "NEVER"
                    or item.get("score_bearing") is not False
                ):
                    raise ValueError(
                        f"{challenge_id} canary must be NEVER calibrated and non-score-bearing"
                    )
                introduced_source_version = _introduced_source_version(
                    item.get("introduced_source_version"),
                    label=challenge_id,
                    source_version=source_version,
                )
                row.update(
                    {
                        "calibration_exposure": "NEVER",
                        "score_bearing": False,
                        "introduced_source_version": introduced_source_version,
                    }
                )
            if require_shadow:
                if item.get("lifecycle") != "SHADOW" or item.get("score_bearing") is not False:
                    raise ValueError(
                        f"{challenge_id} fresh challenge must be SHADOW and non-score-bearing"
                    )
                introduced_source_version = _introduced_source_version(
                    item.get("introduced_source_version"),
                    label=challenge_id,
                    source_version=source_version,
                )
                row.update(
                    {
                        "lifecycle": "SHADOW",
                        "score_bearing": False,
                        "introduced_source_version": introduced_source_version,
                    }
                )
            rows.append(row)
        return rows

    pressure = normalize_rows("contradiction_pressure", role="CONTRADICTION_PRESSURE")
    canaries = normalize_rows("canaries", role="CANARY", require_never_calibrated=True)
    fresh = normalize_rows("fresh_challenges", role="FRESH_SHADOW", require_shadow=True)

    rotation_raw = payload.get("rotation")
    if not isinstance(rotation_raw, dict):
        raise ValueError("benchmark integrity registry requires rotation policy")
    if rotation_raw.get("strategy") != "DETERMINISTIC_SEEDED_WITHOUT_REPLACEMENT":
        raise ValueError("benchmark integrity rotation strategy mismatch")
    rotation: dict[str, Any] = {"strategy": rotation_raw["strategy"]}
    for field, pool in (("canary_slots", canaries), ("fresh_shadow_slots", fresh)):
        value = rotation_raw.get(field)
        if type(value) is not int or value < 1 or value > len(pool):
            raise ValueError(f"benchmark integrity {field} must be between 1 and pool size")
        rotation[field] = value

    base = {
        "schema_version": BENCHMARK_REGISTRY_SCHEMA_VERSION,
        "policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "source_version": source_version,
        "false_premise_question_ids": list(false_premise_ids),
        "equivalence_families": families,
        "contradiction_pressure": pressure,
        "canaries": canaries,
        "fresh_challenges": fresh,
        "rotation": rotation,
    }
    return {**base, "registry_identity": "sha256:" + _canonical_sha256(base)}


def benchmark_integrity_manifest(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a prompt-free summary safe to expose in ordinary exam metadata."""

    families = registry["equivalence_families"]
    hidden_paraphrases = sum(len(family["variants"]) for family in families)
    return {
        "policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "registry_identity": registry["registry_identity"],
        "false_premise_questions": len(registry["false_premise_question_ids"]),
        "equivalence_families": len(families),
        "hidden_paraphrases": hidden_paraphrases,
        "contradiction_pressure_challenges": len(registry["contradiction_pressure"]),
        "canary_questions": len(registry["canaries"]),
        "fresh_shadow_questions": len(registry["fresh_challenges"]),
        "canary_slots_per_challenge_set": registry["rotation"]["canary_slots"],
        "fresh_shadow_slots_per_challenge_set": registry["rotation"]["fresh_shadow_slots"],
        "rotation_strategy": registry["rotation"]["strategy"],
        "canaries_score_bearing": False,
        "fresh_shadow_score_bearing": False,
        "comparison_controls": dict(BENCHMARK_CONTROL_REQUIREMENTS),
        "status": "READY",
        "rule": (
            "hidden variants reuse canonical semantic truth; canaries/fresh challenges are diagnostic until explicitly promoted"
        ),
    }


def _choose_variant(family: dict[str, Any], seed: str) -> dict[str, Any]:
    digest = hashlib.sha256(f"{seed}\0{family['id']}".encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % len(family["variants"])
    return family["variants"][index]


def _select_rotating(
    rows: list[dict[str, Any]], *, slots: int, seed: str, label: str
) -> list[dict[str, Any]]:
    """Select a deterministic seed-bound subset without exposing pool order as authority."""

    ranked = sorted(
        rows,
        key=lambda row: hashlib.sha256(f"{seed}\0{label}\0{row['id']}".encode("utf-8")).hexdigest(),
    )
    return [copy.deepcopy(row) for row in ranked[:slots]]


def _delivery_id(registry_identity: str, challenge_id: str, seed: str) -> str:
    digest = hashlib.sha256(
        f"{registry_identity}\0{challenge_id}\0{seed}".encode("utf-8")
    ).hexdigest()
    return "D-" + digest[:20].upper()


def challenge_set(
    registry: dict[str, Any],
    *,
    questions: Iterable[Any],
    repository_identity: str,
    exam_snapshot_sha256: str,
    seed: str,
) -> dict[str, Any]:
    """Assemble one identity-bound hidden challenge set.

    `public_delivery` is the only portion a harness may expose to the candidate. `secret_bindings`
    remain grader-side so canary/fresh roles and canonical question bindings are not disclosed.
    """

    if not isinstance(seed, str) or not seed.strip() or len(seed) > 256:
        raise ValueError("challenge seed must be a non-empty string of at most 256 characters")
    question_by_id, _ = _question_index(questions)
    chosen: list[tuple[dict[str, Any], str]] = []
    for family in registry["equivalence_families"]:
        selected = _choose_variant(family, seed)
        chosen.append(
            (
                {
                    "id": selected["id"],
                    "question_id": family["question_id"],
                    "prompt": selected["prompt"],
                    "role": "PARAPHRASE",
                    "family_id": family["id"],
                },
                "PARAPHRASE",
            )
        )
    chosen.extend(
        (copy.deepcopy(row), "CONTRADICTION_PRESSURE") for row in registry["contradiction_pressure"]
    )
    chosen.extend(
        (row, "CANARY")
        for row in _select_rotating(
            registry["canaries"],
            slots=registry["rotation"]["canary_slots"],
            seed=seed,
            label="canary",
        )
    )
    chosen.extend(
        (row, "FRESH_SHADOW")
        for row in _select_rotating(
            registry["fresh_challenges"],
            slots=registry["rotation"]["fresh_shadow_slots"],
            seed=seed,
            label="fresh-shadow",
        )
    )

    rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for item, role in chosen:
        qid = item["question_id"]
        question = question_by_id[qid]
        expected_critical = item.get("critical")
        if expected_critical is None and role == "PARAPHRASE":
            family = next(
                family
                for family in registry["equivalence_families"]
                if family["id"] == item["family_id"]
            )
            expected_critical = family["critical"]
        if expected_critical != (getattr(question, "severity", None) == "critical"):
            raise ValueError(f"challenge {item['id']} criticality drifted from canonical question")
        delivery_id = _delivery_id(registry["registry_identity"], item["id"], seed)
        public = {"delivery_id": delivery_id, "prompt": item["prompt"]}
        secret = {
            "delivery_id": delivery_id,
            "challenge_id": item["id"],
            "question_id": qid,
            "role": role,
            "critical": expected_critical,
        }
        if role == "PARAPHRASE":
            secret["family_id"] = item["family_id"]
        rows.append((delivery_id, public, secret))

    rows.sort(key=lambda row: hashlib.sha256(f"{seed}\0{row[0]}".encode("utf-8")).hexdigest())
    base = {
        "artifact_type": "repository-understanding-challenge-set",
        "schema_version": CHALLENGE_INSTANCE_SCHEMA_VERSION,
        "source_version": registry["source_version"],
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": exam_snapshot_sha256,
        "benchmark_registry_identity": registry["registry_identity"],
        "seed_identity": "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest(),
        "secret_rotation_seed": seed,
        "rotation": {
            "strategy": registry["rotation"]["strategy"],
            "canary_pool_size": len(registry["canaries"]),
            "canary_slots": registry["rotation"]["canary_slots"],
            "fresh_shadow_pool_size": len(registry["fresh_challenges"]),
            "fresh_shadow_slots": registry["rotation"]["fresh_shadow_slots"],
        },
        "public_delivery": [row[1] for row in rows],
        "secret_bindings": [row[2] for row in rows],
        "candidate_visibility_rule": "expose public_delivery only; never expose secret_bindings",
    }
    return {**base, "challenge_instance_identity": "sha256:" + _canonical_sha256(base)}


def _registry_challenge_bindings(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for family in registry["equivalence_families"]:
        for variant in family["variants"]:
            expected[variant["id"]] = {
                "challenge_id": variant["id"],
                "question_id": family["question_id"],
                "role": "PARAPHRASE",
                "critical": family["critical"],
                "family_id": family["id"],
                "prompt": variant["prompt"],
            }
    for key, role in (
        ("contradiction_pressure", "CONTRADICTION_PRESSURE"),
        ("canaries", "CANARY"),
        ("fresh_challenges", "FRESH_SHADOW"),
    ):
        for row in registry[key]:
            expected[row["id"]] = {
                "challenge_id": row["id"],
                "question_id": row["question_id"],
                "role": role,
                "critical": row["critical"],
                "prompt": row["prompt"],
            }
    return expected


def _validate_challenge_instance(
    payload: dict[str, Any], registry: dict[str, Any]
) -> dict[str, Any]:
    if payload.get("artifact_type") != "repository-understanding-challenge-set":
        raise ValueError("challenge input artifact_type mismatch")
    if payload.get("schema_version") != CHALLENGE_INSTANCE_SCHEMA_VERSION:
        raise ValueError("challenge input schema mismatch")
    if payload.get("source_version") != registry["source_version"]:
        raise ValueError("challenge input source_version mismatch")
    if payload.get("benchmark_registry_identity") != registry["registry_identity"]:
        raise ValueError("challenge input benchmark registry identity mismatch")
    identity = payload.get("challenge_instance_identity")
    expected_identity = "sha256:" + _canonical_sha256(
        {key: value for key, value in payload.items() if key != "challenge_instance_identity"}
    )
    if identity != expected_identity:
        raise ValueError("challenge instance identity does not match challenge bytes")
    if (
        payload.get("candidate_visibility_rule")
        != "expose public_delivery only; never expose secret_bindings"
    ):
        raise ValueError("challenge instance candidate visibility rule mismatch")
    seed_identity = payload.get("seed_identity")
    if (
        not isinstance(seed_identity, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", seed_identity) is None
    ):
        raise ValueError("challenge instance seed identity is invalid")
    rotation_seed = payload.get("secret_rotation_seed")
    if not isinstance(rotation_seed, str) or not rotation_seed.strip() or len(rotation_seed) > 256:
        raise ValueError("challenge instance secret rotation seed is invalid")
    if seed_identity != "sha256:" + hashlib.sha256(rotation_seed.encode("utf-8")).hexdigest():
        raise ValueError("challenge instance rotation seed identity mismatch")

    public = payload.get("public_delivery")
    secret = payload.get("secret_bindings")
    if not isinstance(public, list) or not isinstance(secret, list) or len(public) != len(secret):
        raise ValueError("challenge instance public/secret bindings are inconsistent")
    public_ids = [row.get("delivery_id") for row in public if isinstance(row, dict)]
    secret_ids = [row.get("delivery_id") for row in secret if isinstance(row, dict)]
    if (
        len(public_ids) != len(public)
        or public_ids != secret_ids
        or len(set(public_ids)) != len(public_ids)
    ):
        raise ValueError("challenge instance delivery identities are inconsistent")
    if any(set(row) != {"delivery_id", "prompt"} for row in public if isinstance(row, dict)):
        raise ValueError("challenge public delivery leaks non-public metadata")

    registry_bindings = _registry_challenge_bindings(registry)
    by_delivery = {row["delivery_id"]: row for row in public}
    selected_families: set[str] = set()
    pressure_expected = {row["id"] for row in registry["contradiction_pressure"]}
    pressure_observed: set[str] = set()
    canary_observed: set[str] = set()
    fresh_observed: set[str] = set()
    for binding in secret:
        if not isinstance(binding, dict):
            raise ValueError("challenge secret binding must be an object")
        challenge_id = binding.get("challenge_id")
        expected = registry_bindings.get(challenge_id)
        if expected is None:
            raise ValueError(
                f"challenge instance contains unknown registry challenge: {challenge_id}"
            )
        for field in ("question_id", "role", "critical"):
            if binding.get(field) != expected[field]:
                raise ValueError(f"challenge {challenge_id} {field} drifted from SECRET registry")
        if by_delivery[binding["delivery_id"]].get("prompt") != expected["prompt"]:
            raise ValueError(f"challenge {challenge_id} prompt drifted from SECRET registry")
        if expected["role"] == "PARAPHRASE":
            if binding.get("family_id") != expected["family_id"]:
                raise ValueError(
                    f"challenge {challenge_id} equivalence family drifted from SECRET registry"
                )
            if expected["family_id"] in selected_families:
                raise ValueError(
                    f"challenge instance selected multiple variants for {expected['family_id']}"
                )
            selected_families.add(expected["family_id"])
        elif expected["role"] == "CONTRADICTION_PRESSURE":
            pressure_observed.add(challenge_id)
        elif expected["role"] == "CANARY":
            canary_observed.add(challenge_id)
        elif expected["role"] == "FRESH_SHADOW":
            fresh_observed.add(challenge_id)
    expected_families = {family["id"] for family in registry["equivalence_families"]}
    if selected_families != expected_families or pressure_observed != pressure_expected:
        raise ValueError(
            "challenge instance does not contain the exact fixed SECRET challenge composition"
        )
    if len(canary_observed) != registry["rotation"]["canary_slots"]:
        raise ValueError("challenge instance canary rotation slot count mismatch")
    if len(fresh_observed) != registry["rotation"]["fresh_shadow_slots"]:
        raise ValueError("challenge instance fresh-shadow rotation slot count mismatch")
    if not canary_observed.issubset({row["id"] for row in registry["canaries"]}):
        raise ValueError("challenge instance contains non-registry canary")
    if not fresh_observed.issubset({row["id"] for row in registry["fresh_challenges"]}):
        raise ValueError("challenge instance contains non-registry fresh challenge")
    expected_canaries = {
        row["id"]
        for row in _select_rotating(
            registry["canaries"],
            slots=registry["rotation"]["canary_slots"],
            seed=rotation_seed,
            label="canary",
        )
    }
    expected_fresh = {
        row["id"]
        for row in _select_rotating(
            registry["fresh_challenges"],
            slots=registry["rotation"]["fresh_shadow_slots"],
            seed=rotation_seed,
            label="fresh-shadow",
        )
    }
    if canary_observed != expected_canaries or fresh_observed != expected_fresh:
        raise ValueError("challenge instance rotating selection does not match SECRET seed policy")
    rotation = payload.get("rotation")
    expected_rotation = {
        "strategy": registry["rotation"]["strategy"],
        "canary_pool_size": len(registry["canaries"]),
        "canary_slots": registry["rotation"]["canary_slots"],
        "fresh_shadow_pool_size": len(registry["fresh_challenges"]),
        "fresh_shadow_slots": registry["rotation"]["fresh_shadow_slots"],
    }
    if rotation != expected_rotation:
        raise ValueError("challenge instance rotation manifest mismatch")
    return payload


def adversarial_report(
    challenge_instance_path: Path,
    observations_path: Path,
    *,
    registry: dict[str, Any],
) -> dict[str, Any]:
    challenge = json.loads(challenge_instance_path.read_text(encoding="utf-8"))
    if not isinstance(challenge, dict):
        raise ValueError("challenge instance must be a JSON object")
    challenge = _validate_challenge_instance(challenge, registry)
    observations = json.loads(observations_path.read_text(encoding="utf-8"))
    if not isinstance(observations, dict):
        raise ValueError("adversarial observations must be a JSON object")
    if observations.get("artifact_type") != "repository-understanding-adversarial-observations":
        raise ValueError("adversarial observations artifact_type mismatch")
    if observations.get("schema_version") != ADVERSARIAL_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("adversarial observations schema mismatch")
    for field in (
        "source_version",
        "repository_identity",
        "exam_snapshot_sha256",
        "benchmark_registry_identity",
        "challenge_instance_identity",
    ):
        expected = (
            challenge.get(field)
            if field != "benchmark_registry_identity"
            else registry["registry_identity"]
        )
        if observations.get(field) != expected:
            raise ValueError(f"adversarial observations {field} mismatch")

    bindings = challenge["secret_bindings"]
    by_delivery = {row["delivery_id"]: row for row in bindings}
    rows = observations.get("challenges")
    if not isinstance(rows, list) or len(rows) != len(bindings):
        raise ValueError("adversarial observations must cover every delivered hidden challenge")
    observed_ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    critical_failures: list[dict[str, Any]] = []
    role_success = Counter()
    role_total = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("adversarial challenge observation must be an object")
        delivery_id = row.get("delivery_id")
        if delivery_id not in by_delivery or delivery_id in observed_ids:
            raise ValueError(f"unknown/duplicate adversarial delivery id: {delivery_id}")
        binding = by_delivery[delivery_id]
        role = binding["role"]
        outcome = row.get("outcome")
        allowed = PRESSURE_OUTCOMES if role == "CONTRADICTION_PRESSURE" else PARAPHRASE_OUTCOMES
        if outcome not in allowed:
            raise ValueError(f"{delivery_id} has invalid outcome for {role}")
        positive = (
            outcome == "EVIDENCE_BACKED_RESISTANCE"
            if role == "CONTRADICTION_PRESSURE"
            else outcome == "CONSISTENT"
        )
        role_total[role] += 1
        role_success[role] += int(positive)
        normalized_row = {
            **binding,
            "outcome": outcome,
            "rationale": row.get("rationale"),
            "passed": positive,
        }
        normalized.append(normalized_row)
        observed_ids.append(delivery_id)
        if binding["critical"] and not positive:
            critical_failures.append(normalized_row)
    if set(observed_ids) != set(by_delivery):
        raise ValueError("adversarial observations do not match delivered challenge set")

    false_rows = observations.get("false_premises")
    expected_false = list(registry["false_premise_question_ids"])
    if not isinstance(false_rows, list) or len(false_rows) != len(expected_false):
        raise ValueError(
            "adversarial observations must cover every canonical false-premise question"
        )
    normalized_false: list[dict[str, Any]] = []
    observed_false: list[str] = []
    for row in false_rows:
        if not isinstance(row, dict):
            raise ValueError("false-premise observation must be an object")
        qid = row.get("question_id")
        state = row.get("premise_response")
        if qid not in expected_false or qid in observed_false:
            raise ValueError(f"unknown/duplicate false-premise question: {qid}")
        if state not in FALSE_PREMISE_RESPONSES:
            raise ValueError(f"{qid} has invalid false-premise response")
        normalized_false.append({"question_id": qid, "premise_response": state})
        observed_false.append(qid)
    if observed_false != expected_false:
        raise ValueError("false-premise observations must use canonical question order")

    def rate(role: str) -> float | None:
        total = role_total[role]
        return None if total == 0 else round(100.0 * role_success[role] / total, 1)

    false_rejected = sum(row["premise_response"] == "REJECTED" for row in normalized_false)
    gate_failures = ["critical_adversarial_failure"] if critical_failures else []
    base = {
        "artifact_type": "repository-understanding-adversarial-result",
        "schema_version": ADVERSARIAL_RESULT_SCHEMA_VERSION,
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "benchmark_integrity_policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "source_version": challenge["source_version"],
        "repository_identity": challenge["repository_identity"],
        "exam_snapshot_sha256": challenge["exam_snapshot_sha256"],
        "benchmark_registry_identity": registry["registry_identity"],
        "challenge_instance_identity": challenge["challenge_instance_identity"],
        "challenge_results": normalized,
        "false_premise_results": normalized_false,
        "paraphrase_consistency_percent": rate("PARAPHRASE"),
        "pressure_resistance_percent": rate("CONTRADICTION_PRESSURE"),
        "canary_consistency_percent": rate("CANARY"),
        "fresh_shadow_consistency_percent": rate("FRESH_SHADOW"),
        "false_premise_rejection_percent": round(100.0 * false_rejected / len(normalized_false), 1),
        "critical_adversarial_failures": critical_failures,
        "adversarial_gate_failures": gate_failures,
        "qualified": not gate_failures,
        "policy_note": (
            "rates are diagnostic and have no invented numeric threshold; only a failed critical semantic challenge is a hard veto"
        ),
    }
    return {**base, "adversarial_result_identity": "sha256:" + _canonical_sha256(base)}


def benchmark_integrity_report(
    execution_evidence_path: Path,
    *,
    registry: dict[str, Any],
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
    challenge_instance_path: Path | None = None,
) -> dict[str, Any]:
    """Validate harness/control-plane evidence required for benchmark comparability."""

    payload = json.loads(execution_evidence_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark execution evidence must be a JSON object")
    if payload.get("artifact_type") != "repository-understanding-benchmark-execution-evidence":
        raise ValueError("benchmark execution evidence artifact_type mismatch")
    if payload.get("schema_version") != BENCHMARK_EXECUTION_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("benchmark execution evidence schema mismatch")
    expected = {
        "source_version": source_version,
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": exam_snapshot_sha256,
        "benchmark_registry_identity": registry["registry_identity"],
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ValueError(f"benchmark execution evidence {field} mismatch")
    run_identity = payload.get("candidate_run_identity")
    if not isinstance(run_identity, str) or not run_identity.strip():
        raise ValueError("benchmark execution evidence candidate_run_identity is required")
    config_identity = payload.get("candidate_configuration_identity")
    if not isinstance(config_identity, str) or not config_identity.startswith("sha256:"):
        raise ValueError(
            "benchmark execution evidence candidate_configuration_identity is required"
        )

    challenge_identity: str | None = None
    if challenge_instance_path is not None:
        challenge_payload = json.loads(challenge_instance_path.read_text(encoding="utf-8"))
        if not isinstance(challenge_payload, dict):
            raise ValueError("challenge instance must be a JSON object")
        challenge_payload = _validate_challenge_instance(challenge_payload, registry)
        challenge_identity = challenge_payload["challenge_instance_identity"]
        if payload.get("challenge_instance_identity") != challenge_identity:
            raise ValueError("benchmark execution evidence challenge_instance_identity mismatch")

    controls = payload.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("benchmark execution evidence controls must be an object")
    failures = [
        key
        for key, required in BENCHMARK_CONTROL_REQUIREMENTS.items()
        if controls.get(key) != required
    ]
    base = {
        "artifact_type": "repository-understanding-benchmark-integrity-result",
        "schema_version": BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION,
        "policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        **expected,
        "challenge_instance_identity": challenge_identity,
        "candidate_run_identity": run_identity,
        "candidate_configuration_identity": config_identity,
        "required_controls": dict(BENCHMARK_CONTROL_REQUIREMENTS),
        "observed_controls": {key: controls.get(key) for key in BENCHMARK_CONTROL_REQUIREMENTS},
        "control_failures": failures,
        "comparison_status": "COMPARABLE" if not failures else "NOT_COMPARABLE",
        "comparable": not failures,
        "authority_note": (
            "this artifact validates harness/control-plane evidence; it does not treat candidate self-attestation as enforcement"
        ),
    }
    return {**base, "integrity_result_identity": "sha256:" + _canonical_sha256(base)}
