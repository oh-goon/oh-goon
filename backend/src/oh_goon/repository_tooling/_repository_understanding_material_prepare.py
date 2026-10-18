"""Private prepared-checkout builder for Repository Understanding material."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from oh_goon.repository_tooling.source_layout import assert_prepared_source_tree


def prepare_exam(destination: Path, *, force: bool = False) -> Path:
    from oh_goon.repository_tooling import repository_understanding_material as material

    exam_manifest = material.verify()
    destination = destination.expanduser().resolve()
    if destination == material.ROOT.resolve() or material._contained(destination, material.ROOT):
        raise ValueError("exam destination must be outside the source checkout")
    if destination.exists():
        if not force:
            raise FileExistsError(f"exam destination already exists: {destination}")
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("refusing to replace a non-directory or symlink destination")
        shutil.rmtree(destination)

    shutil.copytree(material.ROOT, destination, ignore=material._ignore, symlinks=False)
    answer_path = destination / material.ANSWER_RELATIVE
    if answer_path.exists() or answer_path.is_symlink():
        raise RuntimeError("grader SECRET tree leaked into prepared exam checkout")
    assert_prepared_source_tree(destination)

    marker = {
        **exam_manifest,
        "questions": str(material.QUESTION_RELATIVE),
        "public_metadata": str(material.QUESTION_METADATA_RELATIVE),
        "answers_excluded": str(material.ANSWER_RELATIVE),
        "candidate_mode": "read-only",
        "information_classes": {
            "PUBLIC": "candidate may read",
            "EVIDENCE": "candidate may discover through repository",
            "SECRET": "grading authority only; physically excluded from this checkout",
        },
        "benchmark_integrity_contract": {
            "comparison_requires_harness_evidence": True,
            "network_access": "DENIED",
            "prior_conversation_history": "DENIED",
            "prior_candidate_answers": "DENIED",
            "grader_secret_material": "DENIED",
            "candidate_workspace_mutation": "DENIED",
            "fresh_session": "REQUIRED",
            "candidate_configuration_frozen": "REQUIRED",
        },
        "candidate_output": {
            "answer_every_question": True,
            "cite_repository_evidence": True,
            "may_state_insufficient_evidence": True,
            "bounded_confidence_required": True,
            "confidence_values": list(material.CANDIDATE_CONFIDENCE_STATES),
            "explicit_abstention_value": "ABSTAINED",
            "do_not_edit_repository": True,
        },
        "note": "Expose only this prepared checkout to the candidate agent. This copy is not an OS sandbox.",
    }
    (destination / ".oh-goon-certification-exam.json").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )
    return destination
