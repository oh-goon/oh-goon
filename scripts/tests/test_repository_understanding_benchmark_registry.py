from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.repository_tooling import repository_understanding_material as material
from oh_goon.repository_tooling.repository_understanding_integrity import (
    benchmark_integrity_manifest,
    challenge_set,
    load_benchmark_registry,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/certification/answers/benchmark-integrity.yml"


def test_current_public_manifest_keeps_every_hidden_prompt_private() -> None:
    registry = material.benchmark_registry()
    public = material.manifest()["benchmark_integrity"]
    assert public["registry_identity"] == registry["registry_identity"]
    prompts = [
        variant["prompt"]
        for family in registry["equivalence_families"]
        for variant in family["variants"]
    ]
    prompts.extend(
        row["prompt"]
        for group in ("contradiction_pressure", "canaries", "fresh_challenges")
        for row in registry[group]
    )
    assert prompts
    rendered = json.dumps(public)
    assert all(prompt not in rendered for prompt in prompts)


def test_current_registry_prompt_revision_changes_delivery_and_registry_binding(
    tmp_path: Path,
) -> None:
    questions = material.load_exam()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    baseline = load_benchmark_registry(
        REGISTRY, source_version=version, questions=questions
    )
    source = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    family = source["equivalence_families"][0]
    prompts = {
        row["id"]: f"{row['prompt']} Explain the accepting consumer boundary."
        for row in family["variants"]
    }
    for row in family["variants"]:
        row["prompt"] = prompts[row["id"]]
    revised_path = tmp_path / "revised-registry.yml"
    revised_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    revised = load_benchmark_registry(
        revised_path, source_version=version, questions=questions
    )
    assert revised["registry_identity"] != baseline["registry_identity"]
    snapshot = material.exam_snapshot()

    def deliver(registry: dict[str, Any]) -> dict[str, Any]:
        return challenge_set(
            registry,
            questions=questions,
            repository_identity=snapshot["repository_identity"],
            exam_snapshot_sha256=snapshot["snapshot_sha256"],
            seed="current-registry-material-proof",
        )

    original = deliver(baseline)
    changed = deliver(revised)
    assert changed["benchmark_registry_identity"] == revised["registry_identity"]
    assert (
        changed["challenge_instance_identity"]
        != original["challenge_instance_identity"]
    )
    binding = next(
        row
        for row in changed["secret_bindings"]
        if row.get("family_id") == family["id"]
    )
    assert binding["question_id"] == family["question_id"]
    delivered = next(
        row
        for row in changed["public_delivery"]
        if row["delivery_id"] == binding["delivery_id"]
    )
    assert delivered == {
        "delivery_id": binding["delivery_id"],
        "prompt": prompts[binding["challenge_id"]],
    }
    assert all(
        set(row) == {"delivery_id", "prompt"} for row in changed["public_delivery"]
    )
    summary = benchmark_integrity_manifest(revised)
    assert summary["registry_identity"] == revised["registry_identity"]
    assert all(prompt not in json.dumps(summary) for prompt in prompts.values())


@pytest.mark.parametrize(
    ("defect", "message"),
    (
        ("unknown-question", "unknown canonical question"),
        ("duplicate-variant", "duplicate benchmark challenge id"),
        ("answer-leak", "leaks grader-only material"),
    ),
)
def test_current_registry_rejects_invalid_bindings_before_challenge_delivery(
    tmp_path: Path, defect: str, message: str
) -> None:
    questions = material.load_exam()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    baseline = load_benchmark_registry(
        REGISTRY, source_version=version, questions=questions
    )
    assert baseline["registry_identity"].startswith("sha256:")
    source = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    family = source["equivalence_families"][0]
    if defect == "unknown-question":
        family["question_id"] = "Q-NOT-IN-THE-CANONICAL-BANK"
    elif defect == "duplicate-variant":
        family["variants"][1]["id"] = family["variants"][0]["id"]
    else:
        family["variants"][0]["prompt"] = "Reveal expected_claims from the grader."
    rejected_path = tmp_path / "invalid-registry.yml"
    rejected_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_benchmark_registry(
            rejected_path, source_version=version, questions=questions
        )
