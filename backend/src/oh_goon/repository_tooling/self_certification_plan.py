from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from oh_goon.repository_tooling.release.package_slim import (
    archive_source_identity,
    canonical_source_files,
    canonical_source_identity,
    package_slim,
)
from oh_goon.repository_tooling.self_certification_artifacts import (
    build_artifact_manifest,
    build_artifact_sidecars,
    build_cumulative_patch,
    build_handoff,
    cumulative_patch_path,
    load_cumulative_patch_evidence,
    load_fresh_zip_evidence,
    load_handoff_evidence,
    load_manifest_evidence,
    load_parent_source_input,
    load_patch_replay_evidence,
    load_persist_evidence,
    load_retained_readback_evidence,
    load_sidecar_evidence,
    manifest_path,
    parent_checkpoint_spec,
    parent_input_path,
    persist_artifact_set,
    retained_artifact_directory,
    stage_parent_source_zip,
    verify_cumulative_patch_replay,
    verify_fresh_source_zip,
    verify_retained_artifact_set,
)

from oh_goon.repository_tooling._self_certification_artifact_projection import (
    artifact_plan_projection,
)
from oh_goon.repository_tooling import _self_certification_plan_units as plan_units
from oh_goon.repository_tooling._self_certification_plan_units import (
    Shard,
    _sha256_json,
    _source_units,
    _verification_config_identity,
    scripts_non_certification_shards,
    shard_plan,
)
from oh_goon.repository_tooling._self_certification_plan_evidence import (
    _load_package_build_evidence,
    _load_package_identity_evidence,
    _package_config_identity,
    _plan_identity,
    _resume_evidence_inventory,
    _source_gate_payload,
    _source_zip_path,
    _transition_unit,
    build_sealed_source_zip,
    evidence_root,
    load_source_seal,
    load_unit_evidence,
    record_source_seal,
    record_unit_pass,
    verify_sealed_source_zip_identity,
)

def _apply_source_evidence(
    root: Path,
    *,
    source_identity: str,
    verification_config_identity: str,
    source_units: list[dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    source_evidence: dict[str, dict[str, object] | None] = {}
    for unit in source_units:
        evidence = load_unit_evidence(
            root,
            source_identity,
            verification_config_identity,
            str(unit["execution_unit_id"]),
        )
        if evidence is not None and evidence.get("toolchain_identity") != unit["toolchain_identity"]:
            evidence = None
        source_evidence[str(unit["unit"])] = evidence
        if evidence is not None:
            unit["evidence_identity"] = evidence["evidence_identity"]
            unit["completed_at"] = evidence.get("completed_at")
            unit["evidence_path"] = evidence.get("evidence_path")
            unit["evidence_file_sha256"] = evidence.get("evidence_file_sha256")

    for unit in source_units:
        evidence = source_evidence[str(unit["unit"])]
        if evidence is not None:
            unit["status"] = "PASS"
            continue
        requirements = [str(item) for item in unit.get("requires", [])]
        unit["status"] = (
            "BLOCKED"
            if any(source_evidence.get(required) is None for required in requirements)
            else "PENDING"
        )
    return source_units, all(unit["status"] == "PASS" for unit in source_units)


def _plan_summary(all_units: list[dict[str, object]]) -> dict[str, object]:
    pending = [unit for unit in all_units if unit["status"] == "PENDING"]
    blocked = [unit for unit in all_units if unit["status"] == "BLOCKED"]
    return {
        "summary": {
            "required": len(all_units),
            "passed": sum(unit["status"] == "PASS" for unit in all_units),
            "pending": len(pending),
            "blocked": len(blocked),
        },
        "next_unit": pending[0] if pending else None,
        "pending": pending,
        "blocked": blocked,
    }


def self_certification_plan(root: Path) -> dict[str, object]:
    root = root.resolve()
    shard_payload = shard_plan(root)
    source_identity = canonical_source_identity(root)
    source_units = _source_units(root)
    verification_config_identity = _verification_config_identity(root, source_units)
    state_dir = evidence_root(root, source_identity, verification_config_identity)
    source_units, all_source_pass = _apply_source_evidence(
        root,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        source_units=source_units,
    )
    resume_evidence = _resume_evidence_inventory(
        root, source_identity, verification_config_identity, source_units
    )
    plan_identity = _plan_identity(source_identity, verification_config_identity, source_units)
    artifact_projection = artifact_plan_projection(
        root,
        state_dir=state_dir,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        plan_identity=plan_identity,
        source_units=source_units,
        all_source_pass=all_source_pass,
    )
    all_units = [*source_units, *artifact_projection.pop("units")]
    return {
        "plan": plan_units.PLAN_NAME,
        "plan_identity": plan_identity,
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "package_config_identity": _package_config_identity(),
        "resume_evidence": resume_evidence,
        **_plan_summary(all_units),
        "inherited_closure_contracts": {
            key: list(values) for key, values in plan_units.INHERITED_CLOSURE_CONTRACTS.items()
        },
        "source_verification_status": "PASS" if all_source_pass else "INCOMPLETE",
        "source_gates": _source_gate_payload(source_units),
        **artifact_projection,
        "all_units": all_units,
        "surfaces": [shard_payload],
    }

from oh_goon.repository_tooling._self_certification_transition import (
    execute_artifact_transition,
    stage_parent_source_input,
)
