from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from oh_goon.repository_tooling._self_certification_artifact_transitions import (
    _fresh_zip_projection,
    _handoff_projection,
    _manifest_projection,
    _package_build_projection,
    _package_identity_projection,
    _patch_projection,
    _persist_projection,
    _readback_projection,
    _replay_projection,
    _seal_projection,
    _sidecar_projection,
)
from oh_goon.repository_tooling._self_certification_plan_evidence import _source_zip_path
from oh_goon.repository_tooling.self_certification_artifacts import (
    cumulative_patch_path,
    manifest_path,
    parent_input_path,
    retained_artifact_directory,
)

Evidence = dict[str, object] | None
Unit = dict[str, object]


class PackageChain(TypedDict):
    units: list[Unit]
    seal: Evidence
    seal_identity: str
    build_evidence: Evidence
    identity_evidence: Evidence
    fresh_evidence: Evidence
    source_zip_sha256: str


class RetentionChain(TypedDict):
    units: list[Unit]
    parent_spec: dict[str, object]
    parent_ready: bool
    patch_evidence: Evidence
    replay_evidence: Evidence
    manifest_evidence: Evidence
    persist_evidence: Evidence
    readback_evidence: Evidence


class ArtifactProjection(TypedDict):
    checkpoint_state: str
    status: str
    source_seal: dict[str, object]
    required_inputs: list[dict[str, object]]
    artifact_status: str
    artifact: dict[str, object]


class ArtifactPlanProjection(ArtifactProjection):
    units: list[Unit]


def _artifact_status(
    *,
    build_evidence: Evidence,
    identity_evidence: Evidence,
    replay_evidence: Evidence,
    manifest_evidence: Evidence,
    persist_evidence: Evidence,
    readback_evidence: Evidence,
) -> str:
    if readback_evidence is not None:
        return "RETAINED_VERIFIED"
    if persist_evidence is not None:
        return "RETAINED"
    if manifest_evidence is not None:
        return "MANIFESTED"
    if replay_evidence is not None:
        return "PATCH_REPLAY_VERIFIED"
    if identity_evidence is not None:
        return "SOURCE_IDENTITY_VERIFIED"
    return "ZIP_BUILT" if build_evidence is not None else "BLOCKED"


def _checkpoint_state(
    *,
    seal: Evidence,
    waiting_for_parent: bool,
    manifest_evidence: Evidence,
    persist_evidence: Evidence,
    readback_evidence: Evidence,
) -> str:
    if manifest_evidence is not None and persist_evidence is None:
        return "READY_TO_PERSIST"
    if readback_evidence is not None:
        return "PERSISTED_VERIFIED"
    if waiting_for_parent:
        return "WAITING_FOR_INPUT"
    return "SOURCE_SEALED" if seal is not None else "VERIFYING_SOURCE"


def _overall_status(
    *, seal: Evidence, waiting_for_parent: bool, readback_evidence: Evidence
) -> str:
    if readback_evidence is not None:
        return "ARTIFACT_SET_RETAINED"
    if waiting_for_parent:
        return "WAITING_FOR_INPUT"
    return "SEALED" if seal is not None else "INCOMPLETE"


def _source_package_chain(
    root: Path,
    *,
    state_dir: Path,
    source_identity: str,
    verification_config_identity: str,
    plan_identity: str,
    source_units: list[dict[str, object]],
    all_source_pass: bool,
) -> PackageChain:
    seal_unit, seal, seal_identity = _seal_projection(
        root,
        source_identity,
        verification_config_identity,
        plan_identity,
        source_units,
        all_source_pass,
    )
    build_unit, build_evidence = _package_build_projection(
        root, source_identity, verification_config_identity, seal_identity, seal
    )
    identity_unit, identity_evidence = _package_identity_projection(
        root,
        source_identity,
        verification_config_identity,
        seal_identity,
        build_evidence,
    )
    source_zip_sha256 = str(build_evidence["artifact_sha256"]) if build_evidence is not None else ""
    fresh_unit, fresh_evidence = _fresh_zip_projection(
        state_dir,
        source_zip_sha256,
        source_identity,
        seal_identity,
        identity_evidence,
    )
    return {
        "units": [seal_unit, build_unit, identity_unit, fresh_unit],
        "seal": seal,
        "seal_identity": seal_identity,
        "build_evidence": build_evidence,
        "identity_evidence": identity_evidence,
        "fresh_evidence": fresh_evidence,
        "source_zip_sha256": source_zip_sha256,
    }


def _retention_chain(
    root: Path,
    *,
    state_dir: Path,
    source_zip_sha256: str,
    fresh_evidence: Evidence,
    seal_identity: str,
) -> RetentionChain:
    patch_unit, patch_evidence, parent_spec, parent_input = _patch_projection(
        root, state_dir, source_zip_sha256, fresh_evidence
    )
    replay_unit, replay_evidence = _replay_projection(state_dir, source_zip_sha256, patch_evidence)
    handoff_unit, handoff_evidence = _handoff_projection(
        root,
        state_dir,
        source_zip_sha256,
        patch_evidence,
        replay_evidence,
        fresh_evidence,
    )
    sidecar_unit, sidecar_evidence = _sidecar_projection(
        root, state_dir, source_zip_sha256, patch_evidence, handoff_evidence
    )
    manifest_unit, manifest_evidence = _manifest_projection(
        root,
        state_dir,
        sidecar_evidence,
        replay_evidence,
        fresh_evidence,
        seal_identity,
    )
    persist_unit, persist_evidence = _persist_projection(root, state_dir, manifest_evidence)
    readback_unit, readback_evidence = _readback_projection(
        root, state_dir, manifest_evidence, persist_evidence
    )
    return {
        "units": [
            patch_unit,
            replay_unit,
            handoff_unit,
            sidecar_unit,
            manifest_unit,
            persist_unit,
            readback_unit,
        ],
        "parent_spec": parent_spec,
        "parent_ready": parent_input is not None,
        "patch_evidence": patch_evidence,
        "replay_evidence": replay_evidence,
        "manifest_evidence": manifest_evidence,
        "persist_evidence": persist_evidence,
        "readback_evidence": readback_evidence,
    }


def _source_seal_payload(seal: Evidence, all_source_pass: bool) -> dict[str, object]:
    if seal is not None:
        return {"status": "SEALED", "seal_identity": seal.get("seal_identity")}
    return {
        "status": "READY" if all_source_pass else "BLOCKED",
        "seal_identity": None,
    }


def _required_parent_input_payload(
    root: Path,
    state_dir: Path,
    parent_spec: dict[str, object],
    parent_ready: bool,
) -> list[dict[str, object]]:
    return [
        {
            "input": "parent-source-zip",
            "status": "VERIFIED" if parent_ready else "MISSING",
            "artifact": parent_spec["artifact"],
            "version": parent_spec["version"],
            "sha256": parent_spec["sha256"],
            "stage_command": [
                "backend/.venv/bin/oh-goon",
                "self",
                "setup",
                "--parent-zip",
                f"<path-to-{parent_spec['artifact']}>",
            ],
            "staged_path": str(parent_input_path(root, state_dir)) if parent_ready else None,
        }
    ]


def _artifact_payload(
    root: Path,
    *,
    state_dir: Path,
    source_identity: str,
    verification_config_identity: str,
    build_evidence: Evidence,
    patch_evidence: Evidence,
    manifest_evidence: Evidence,
    readback_evidence: Evidence,
) -> dict[str, object]:
    source_zip = None
    source_zip_sha256 = None
    cumulative_patch = None
    cumulative_patch_sha256 = None
    manifest = None
    artifact_set_identity = None
    retained_directory = None
    if build_evidence is not None:
        source_zip = str(_source_zip_path(root, source_identity, verification_config_identity))
        source_zip_sha256 = build_evidence.get("artifact_sha256")
    if patch_evidence is not None:
        cumulative_patch = str(cumulative_patch_path(root, state_dir))
        cumulative_patch_sha256 = patch_evidence.get("artifact_sha256")
    if manifest_evidence is not None:
        manifest = str(manifest_path(root, state_dir))
        artifact_set_identity = manifest_evidence.get("artifact_set_identity")
    if readback_evidence is not None and artifact_set_identity is not None:
        retained_directory = str(retained_artifact_directory(state_dir, str(artifact_set_identity)))
    return {
        "source_zip": source_zip,
        "source_zip_sha256": source_zip_sha256,
        "cumulative_patch": cumulative_patch,
        "cumulative_patch_sha256": cumulative_patch_sha256,
        "manifest": manifest,
        "artifact_set_identity": artifact_set_identity,
        "retained_directory": retained_directory,
    }


def _projection_payload(
    root: Path,
    *,
    state_dir: Path,
    source_identity: str,
    verification_config_identity: str,
    all_source_pass: bool,
    package_chain: PackageChain,
    retention_chain: RetentionChain,
) -> ArtifactProjection:
    seal = package_chain["seal"]
    parent_ready = bool(retention_chain["parent_ready"])
    fresh_evidence = package_chain["fresh_evidence"]
    waiting_for_parent = fresh_evidence is not None and not parent_ready
    build_evidence = package_chain["build_evidence"]
    identity_evidence = package_chain["identity_evidence"]
    replay_evidence = retention_chain["replay_evidence"]
    manifest_evidence = retention_chain["manifest_evidence"]
    persist_evidence = retention_chain["persist_evidence"]
    readback_evidence = retention_chain["readback_evidence"]
    return {
        "checkpoint_state": _checkpoint_state(
            seal=seal,
            waiting_for_parent=waiting_for_parent,
            manifest_evidence=manifest_evidence,
            persist_evidence=persist_evidence,
            readback_evidence=readback_evidence,
        ),
        "status": _overall_status(
            seal=seal,
            waiting_for_parent=waiting_for_parent,
            readback_evidence=readback_evidence,
        ),
        "source_seal": _source_seal_payload(seal, all_source_pass),
        "required_inputs": _required_parent_input_payload(
            root, state_dir, retention_chain["parent_spec"], parent_ready
        ),
        "artifact_status": _artifact_status(
            build_evidence=build_evidence,
            identity_evidence=identity_evidence,
            replay_evidence=replay_evidence,
            manifest_evidence=manifest_evidence,
            persist_evidence=persist_evidence,
            readback_evidence=readback_evidence,
        ),
        "artifact": _artifact_payload(
            root,
            state_dir=state_dir,
            source_identity=source_identity,
            verification_config_identity=verification_config_identity,
            build_evidence=build_evidence,
            patch_evidence=retention_chain["patch_evidence"],
            manifest_evidence=manifest_evidence,
            readback_evidence=readback_evidence,
        ),
    }


def artifact_plan_projection(
    root: Path,
    *,
    state_dir: Path,
    source_identity: str,
    verification_config_identity: str,
    plan_identity: str,
    source_units: list[dict[str, object]],
    all_source_pass: bool,
) -> ArtifactPlanProjection:
    """Read already-recorded artifact evidence and derive the plan projection only."""
    package_chain = _source_package_chain(
        root,
        state_dir=state_dir,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        plan_identity=plan_identity,
        source_units=source_units,
        all_source_pass=all_source_pass,
    )
    retention_chain = _retention_chain(
        root,
        state_dir=state_dir,
        source_zip_sha256=str(package_chain["source_zip_sha256"]),
        fresh_evidence=package_chain["fresh_evidence"],
        seal_identity=str(package_chain["seal_identity"]),
    )
    payload = _projection_payload(
        root,
        state_dir=state_dir,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        all_source_pass=all_source_pass,
        package_chain=package_chain,
        retention_chain=retention_chain,
    )
    return {
        "units": [*package_chain["units"], *retention_chain["units"]],
        **payload,
    }
