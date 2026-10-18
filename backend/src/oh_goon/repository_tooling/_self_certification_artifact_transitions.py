from __future__ import annotations

from pathlib import Path
from typing import Any

from oh_goon.repository_tooling._self_certification_plan_evidence import (
    _load_package_build_evidence,
    _load_package_identity_evidence,
    _package_config_identity,
    _transition_unit,
    load_source_seal,
)
from oh_goon.repository_tooling._self_certification_plan_units import _sha256_json
from oh_goon.repository_tooling.self_certification_artifacts import (
    load_cumulative_patch_evidence,
    load_fresh_zip_evidence,
    load_handoff_evidence,
    load_manifest_evidence,
    load_parent_source_input,
    load_patch_replay_evidence,
    load_persist_evidence,
    load_retained_readback_evidence,
    load_sidecar_evidence,
    parent_checkpoint_spec,
)

Evidence = dict[str, Any] | None
Unit = dict[str, object]


def _seal_projection(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    plan_identity: str,
    source_units: list[dict[str, object]],
    all_source_pass: bool,
) -> tuple[Unit, Evidence, str]:
    seal = load_source_seal(
        root,
        source_identity,
        verification_config_identity,
        plan_identity,
        source_units,
    )
    unit: Unit = {
        "unit": "seal-source",
        "surface": "source-seal",
        "strategy": "transition",
        "execution_unit_id": _sha256_json(
            {"transition": "seal-source", "plan_identity": plan_identity}
        ),
        "execution_budget_seconds": 30,
        "command": [
            "backend/.venv/bin/oh-goon",
            "self",
            "verify",
            "--run-unit",
            "seal-source",
        ],
        "status": "PASS" if seal is not None else ("PENDING" if all_source_pass else "BLOCKED"),
        "requires": [str(source_unit["unit"]) for source_unit in source_units],
    }
    if seal is not None:
        unit["seal_identity"] = seal["seal_identity"]
    return unit, seal, str(seal["seal_identity"]) if seal is not None else ""


def _package_build_projection(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    seal: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        _load_package_build_evidence(
            root, source_identity, verification_config_identity, seal_identity
        )
        if seal is not None
        else None
    )
    unit = _transition_unit(
        unit="package/source-zip",
        surface="canonical-source-package",
        status="PASS" if evidence is not None else ("PENDING" if seal is not None else "BLOCKED"),
        budget=120,
        requires=["seal-source"],
        identity_payload={
            "source_identity": source_identity,
            "seal_identity": seal_identity,
            "package_config_identity": _package_config_identity(),
        },
        evidence=evidence,
    )
    if evidence is not None:
        unit["artifact_sha256"] = evidence["artifact_sha256"]
    return unit, evidence


def _package_identity_projection(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    build_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        _load_package_identity_evidence(
            root,
            source_identity,
            verification_config_identity,
            seal_identity,
            build_evidence,
        )
        if build_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/verify-source-identity",
        surface="package-source-identity",
        status="PASS"
        if evidence is not None
        else ("PENDING" if build_evidence is not None else "BLOCKED"),
        budget=60,
        requires=["package/source-zip"],
        identity_payload={
            "source_identity": source_identity,
            "package_config_identity": _package_config_identity(),
            "build_evidence_identity": build_evidence.get("evidence_identity")
            if build_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence


def _fresh_zip_projection(
    state_dir: Path,
    source_zip_sha256: str,
    source_identity: str,
    seal_identity: str,
    identity_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_fresh_zip_evidence(state_dir, source_zip_sha256, source_identity, seal_identity)
        if identity_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/fresh-zip-acceptance",
        surface="fresh-package-acceptance",
        status="PASS"
        if evidence is not None
        else ("PENDING" if identity_evidence is not None else "BLOCKED"),
        budget=60,
        requires=["package/verify-source-identity"],
        identity_payload={
            "source_zip_sha256": source_zip_sha256,
            "source_identity": source_identity,
            "seal_identity": seal_identity,
        },
        evidence=evidence,
    )
    return unit, evidence


def _patch_projection(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    fresh_zip_evidence: Evidence,
) -> tuple[Unit, Evidence, dict[str, object], object | None]:
    parent_spec = parent_checkpoint_spec(root)
    parent_input = load_parent_source_input(root, state_dir)
    evidence = (
        load_cumulative_patch_evidence(root, state_dir, source_zip_sha256, parent_input)
        if fresh_zip_evidence is not None and parent_input is not None
        else None
    )
    unit = _transition_unit(
        unit="package/cumulative-patch",
        surface="cumulative-patch",
        status="PASS"
        if evidence is not None
        else (
            "PENDING" if fresh_zip_evidence is not None and parent_input is not None else "BLOCKED"
        ),
        budget=120,
        requires=["package/fresh-zip-acceptance", "input/parent-source-zip"],
        identity_payload={
            "source_zip_sha256": source_zip_sha256,
            "parent_sha256": parent_spec["sha256"],
        },
        evidence=evidence,
    )
    return unit, evidence, parent_spec, parent_input


def _replay_projection(
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_patch_replay_evidence(state_dir, source_zip_sha256, patch_evidence)
        if patch_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/verify-patch-replay",
        surface="patch-replay",
        status="PASS"
        if evidence is not None
        else ("PENDING" if patch_evidence is not None else "BLOCKED"),
        budget=120,
        requires=["package/cumulative-patch"],
        identity_payload={
            "source_zip_sha256": source_zip_sha256,
            "patch_evidence_identity": patch_evidence.get("evidence_identity")
            if patch_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence


def _handoff_projection(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: Evidence,
    replay_evidence: Evidence,
    fresh_zip_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_handoff_evidence(
            root,
            state_dir,
            source_zip_sha256,
            patch_evidence,
            replay_evidence,
            fresh_zip_evidence,
        )
        if replay_evidence is not None
        and fresh_zip_evidence is not None
        and patch_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/handoff",
        surface="checkpoint-handoff",
        status="PASS"
        if evidence is not None
        else ("PENDING" if replay_evidence is not None else "BLOCKED"),
        budget=30,
        requires=["package/verify-patch-replay"],
        identity_payload={
            "replay_evidence_identity": replay_evidence.get("evidence_identity")
            if replay_evidence
            else None,
            "fresh_zip_evidence_identity": fresh_zip_evidence.get("evidence_identity")
            if fresh_zip_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence


def _sidecar_projection(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: Evidence,
    handoff_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_sidecar_evidence(root, state_dir, source_zip_sha256, patch_evidence, handoff_evidence)
        if handoff_evidence is not None and patch_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/sidecars",
        surface="artifact-sidecars",
        status="PASS"
        if evidence is not None
        else ("PENDING" if handoff_evidence is not None else "BLOCKED"),
        budget=30,
        requires=["package/handoff"],
        identity_payload={
            "handoff_evidence_identity": handoff_evidence.get("evidence_identity")
            if handoff_evidence
            else None,
            "patch_evidence_identity": patch_evidence.get("evidence_identity")
            if patch_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence


def _manifest_projection(
    root: Path,
    state_dir: Path,
    sidecar_evidence: Evidence,
    replay_evidence: Evidence,
    fresh_zip_evidence: Evidence,
    seal_identity: str,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_manifest_evidence(
            root,
            state_dir,
            _package_config_identity(),
            sidecar_evidence,
            replay_evidence,
            fresh_zip_evidence,
        )
        if sidecar_evidence is not None
        and replay_evidence is not None
        and fresh_zip_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/manifest",
        surface="artifact-manifest",
        status="PASS"
        if evidence is not None
        else ("PENDING" if sidecar_evidence is not None else "BLOCKED"),
        budget=30,
        requires=["package/sidecars"],
        identity_payload={
            "sidecar_evidence_identity": sidecar_evidence.get("evidence_identity")
            if sidecar_evidence
            else None,
            "seal_identity": seal_identity,
        },
        evidence=evidence,
    )
    return unit, evidence


def _persist_projection(
    root: Path,
    state_dir: Path,
    manifest_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_persist_evidence(root, state_dir, manifest_evidence)
        if manifest_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/persist",
        surface="local-artifact-retention",
        status="PASS"
        if evidence is not None
        else ("PENDING" if manifest_evidence is not None else "BLOCKED"),
        budget=60,
        requires=["package/manifest"],
        identity_payload={
            "artifact_set_identity": manifest_evidence.get("artifact_set_identity")
            if manifest_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence


def _readback_projection(
    root: Path,
    state_dir: Path,
    manifest_evidence: Evidence,
    persist_evidence: Evidence,
) -> tuple[Unit, Evidence]:
    evidence = (
        load_retained_readback_evidence(root, state_dir, manifest_evidence, persist_evidence)
        if persist_evidence is not None and manifest_evidence is not None
        else None
    )
    unit = _transition_unit(
        unit="package/verify-persistence",
        surface="retained-artifact-readback",
        status="PASS"
        if evidence is not None
        else ("PENDING" if persist_evidence is not None else "BLOCKED"),
        budget=60,
        requires=["package/persist"],
        identity_payload={
            "persist_evidence_identity": persist_evidence.get("evidence_identity")
            if persist_evidence
            else None,
        },
        evidence=evidence,
    )
    return unit, evidence
