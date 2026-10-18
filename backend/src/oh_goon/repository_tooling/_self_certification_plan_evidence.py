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

from oh_goon.repository_tooling import _self_certification_plan_units as plan_units
from oh_goon.repository_tooling._self_certification_plan_units import (
    _sha256_json,
    _source_gate_dependencies,
)

from oh_goon.core.file_digest import sha256_file

def evidence_root(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
) -> Path:
    del root  # Evidence reuse follows exact identities, not extraction path.
    state_home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return (
        state_home
        / "oh-goon"
        / "self-certification"
        / source_identity.removeprefix("sha256:")
        / verification_config_identity.removeprefix("sha256:")
    )


def _atomic_json_write(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return path
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_unit_evidence(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    execution_unit_id: str,
) -> dict[str, object] | None:
    path = evidence_root(root, source_identity, verification_config_identity) / "units" / (
        execution_unit_id.removeprefix("sha256:") + ".json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    expected = {
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "execution_unit_id": execution_unit_id,
        "status": "PASS",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    evidence_identity = payload.get("evidence_identity")
    base = dict(payload)
    base.pop("evidence_identity", None)
    if evidence_identity != _sha256_json(base):
        return None
    enriched = dict(payload)
    enriched["evidence_path"] = str(path)
    enriched["evidence_file_sha256"] = sha256_file(path)
    return enriched


def record_unit_pass(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    unit: dict[str, object],
) -> Path:
    base: dict[str, object] = {
        "schema": 3,
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "execution_unit_id": unit["execution_unit_id"],
        "unit": unit["unit"],
        "toolchain_identity": unit["toolchain_identity"],
        "evidence_level": unit.get("evidence_level", "L1_LOGIC"),
        "result": "PASS",
        "status": "PASS",
        "completed_at": datetime.now(UTC).isoformat(),
    }
    payload = dict(base)
    payload["evidence_identity"] = _sha256_json(base)
    destination = evidence_root(root, source_identity, verification_config_identity) / "units" / (
        str(unit["execution_unit_id"]).removeprefix("sha256:") + ".json"
    )
    return _atomic_json_write(destination, payload)


def _plan_identity(
    source_identity: str,
    verification_config_identity: str,
    source_units: list[dict[str, object]],
) -> str:
    return _sha256_json(
        {
            "plan": plan_units.PLAN_NAME,
            "source_identity": source_identity,
            "verification_config_identity": verification_config_identity,
            "source_gate_dependencies": _source_gate_dependencies(source_units),
            "inherited_closure_contracts": plan_units.INHERITED_CLOSURE_CONTRACTS,
            "required_source_units": [unit["execution_unit_id"] for unit in source_units],
        }
    )


def load_source_seal(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    plan_identity: str,
    source_units: list[dict[str, object]],
) -> dict[str, object] | None:
    path = evidence_root(root, source_identity, verification_config_identity) / "source-seal.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("state") != "SEALED":
        return None
    if payload.get("source_identity") != source_identity:
        return None
    if payload.get("verification_config_identity") != verification_config_identity:
        return None
    if payload.get("plan_identity") != plan_identity:
        return None
    required = payload.get("required_evidence")
    if not isinstance(required, dict):
        return None
    current: dict[str, str] = {}
    for unit in source_units:
        evidence = load_unit_evidence(
            root,
            source_identity,
            verification_config_identity,
            str(unit["execution_unit_id"]),
        )
        if evidence is None:
            return None
        current[str(unit["unit"])] = str(evidence["evidence_identity"])
    if required != current:
        return None
    seal_identity = payload.get("seal_identity")
    base = dict(payload)
    base.pop("seal_identity", None)
    if seal_identity != _sha256_json(base):
        return None
    return payload


def record_source_seal(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    plan_identity: str,
    source_units: list[dict[str, object]],
) -> Path:
    required: dict[str, str] = {}
    for unit in source_units:
        evidence = load_unit_evidence(
            root,
            source_identity,
            verification_config_identity,
            str(unit["execution_unit_id"]),
        )
        if evidence is None:
            raise RuntimeError(f"cannot seal source; missing current PASS evidence for {unit['unit']}")
        required[str(unit["unit"])] = str(evidence["evidence_identity"])
    base: dict[str, object] = {
        "schema": 1,
        "state": "SEALED",
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "plan_identity": plan_identity,
        "required_evidence": required,
    }
    payload = dict(base)
    payload["seal_identity"] = _sha256_json(base)
    destination = evidence_root(root, source_identity, verification_config_identity) / "source-seal.json"
    return _atomic_json_write(destination, payload)



def _package_config_identity() -> str:
    return _sha256_json(
        {
            "builder": "package-slim-v2",
            "staging": "sealed-canonical-source-copy-v1",
            "archive_source_identity": "path-content-v1",
            "artifact_pipeline": "checkpoint-artifacts-v1",
            "cumulative_patch": "git-no-index-binary-v1",
        }
    )


def _artifact_directory(root: Path, source_identity: str, verification_config_identity: str) -> Path:
    return evidence_root(root, source_identity, verification_config_identity) / "artifacts"


def _source_zip_path(root: Path, source_identity: str, verification_config_identity: str) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return _artifact_directory(root, source_identity, verification_config_identity) / f"oh-goon-{version}.zip"


def _copy_canonical_source(root: Path, staging: Path) -> None:
    for path in canonical_source_files(root):
        relative = path.relative_to(root)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def _load_package_build_evidence(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
) -> dict[str, object] | None:
    path = evidence_root(root, source_identity, verification_config_identity) / "package-source-zip.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    expected = {
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "seal_identity": seal_identity,
        "package_config_identity": _package_config_identity(),
        "status": "PASS",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    archive = _source_zip_path(root, source_identity, verification_config_identity)
    if not archive.is_file():
        return None
    digest = sha256_file(archive)
    if payload.get("artifact_sha256") != digest:
        return None
    base = dict(payload)
    evidence_identity = base.pop("evidence_identity", None)
    if evidence_identity != _sha256_json(base):
        return None
    return payload


def build_sealed_source_zip(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
) -> Path:
    artifact_dir = _artifact_directory(root, source_identity, verification_config_identity)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    final_archive = _source_zip_path(root, source_identity, verification_config_identity)
    with tempfile.TemporaryDirectory(prefix="oh-goon-sealed-source-", dir=artifact_dir) as directory:
        temporary_root = Path(directory) / "source"
        temporary_root.mkdir()
        _copy_canonical_source(root, temporary_root)
        observed_source_identity = canonical_source_identity(temporary_root)
        if observed_source_identity != source_identity:
            raise RuntimeError("sealed source changed while creating immutable packaging staging")
        temporary_archive = Path(directory) / final_archive.name
        digest = package_slim(temporary_archive, root=temporary_root)
        final_archive.parent.mkdir(parents=True, exist_ok=True)
        temporary_archive.replace(final_archive)
        temporary_sidecar = temporary_archive.with_name(temporary_archive.name + ".sha256")
        if temporary_sidecar.exists():
            temporary_sidecar.replace(final_archive.with_name(final_archive.name + ".sha256"))
    base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "seal_identity": seal_identity,
        "package_config_identity": _package_config_identity(),
        "artifact": final_archive.name,
        "artifact_sha256": digest,
        "byte_count": final_archive.stat().st_size,
    }
    payload = dict(base)
    payload["evidence_identity"] = _sha256_json(base)
    _atomic_json_write(
        evidence_root(root, source_identity, verification_config_identity) / "package-source-zip.json",
        payload,
    )
    return final_archive


def _load_package_identity_evidence(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    build_evidence: dict[str, object],
) -> dict[str, object] | None:
    path = evidence_root(root, source_identity, verification_config_identity) / "package-source-identity.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    expected = {
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "seal_identity": seal_identity,
        "package_build_evidence_identity": build_evidence["evidence_identity"],
        "status": "MATCH",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    base = dict(payload)
    evidence_identity = base.pop("evidence_identity", None)
    if evidence_identity != _sha256_json(base):
        return None
    archive = _source_zip_path(root, source_identity, verification_config_identity)
    try:
        observed = archive_source_identity(archive)
    except (OSError, ValueError):
        return None
    if observed != source_identity or payload.get("archive_source_identity") != observed:
        return None
    return payload


def verify_sealed_source_zip_identity(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    build_evidence: dict[str, object],
) -> Path:
    archive = _source_zip_path(root, source_identity, verification_config_identity)
    observed = archive_source_identity(archive)
    if observed != source_identity:
        raise RuntimeError(
            f"packaged source identity mismatch: sealed={source_identity} archive={observed}"
        )
    base: dict[str, object] = {
        "schema": 1,
        "status": "MATCH",
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "seal_identity": seal_identity,
        "package_build_evidence_identity": build_evidence["evidence_identity"],
        "archive_source_identity": observed,
    }
    payload = dict(base)
    payload["evidence_identity"] = _sha256_json(base)
    destination = evidence_root(root, source_identity, verification_config_identity) / "package-source-identity.json"
    return _atomic_json_write(destination, payload)

def _source_gate_payload(units: list[dict[str, object]]) -> list[dict[str, object]]:
    statuses = {str(unit["unit"]): str(unit["status"]) for unit in units}
    gates: list[dict[str, object]] = []
    for gate, dependencies in _source_gate_dependencies(units).items():
        gates.append(
            {
                "gate": gate,
                "status": "PASS" if all(statuses.get(name) == "PASS" for name in dependencies) else "PENDING",
                "evidence_units": list(dependencies),
                "evidence_levels": sorted({str(next(unit.get("evidence_level", "L1_LOGIC") for unit in units if str(unit["unit"]) == name)) for name in dependencies}),
            }
        )
    return gates


def _transition_unit(
    *,
    unit: str,
    surface: str,
    status: str,
    budget: int,
    requires: list[str],
    identity_payload: dict[str, object],
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "unit": unit,
        "phase": "artifact",
        "surface": surface,
        "strategy": "transition",
        "execution_unit_id": _sha256_json({"transition": unit, **identity_payload}),
        "execution_budget_seconds": budget,
        "command": ["backend/.venv/bin/oh-goon", "self", "verify", "--run-unit", unit],
        "status": status,
        "requires": requires,
    }
    if evidence is not None:
        payload["evidence_identity"] = evidence.get("evidence_identity")
    return payload




def _resume_evidence_inventory(
    root: Path,
    source_identity: str,
    verification_config_identity: str,
    source_units: list[dict[str, object]],
) -> dict[str, object]:
    """Describe reusable and invalidated source-unit evidence without widening authority.

    Only PASS evidence in the exact current identity namespace can authorize reuse.
    Sibling verification namespaces for the same source are inspected solely to explain
    why previously completed work is no longer reusable.
    """
    current_root = evidence_root(root, source_identity, verification_config_identity)
    source_root = current_root.parent
    invalidated: list[dict[str, object]] = []
    proven: list[str] = []
    pending: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []

    sibling_payloads: dict[str, list[dict[str, object]]] = {}
    try:
        siblings = [path for path in source_root.iterdir() if path.is_dir() and path != current_root]
    except OSError:
        siblings = []
    for sibling in siblings:
        units_dir = sibling / "units"
        try:
            candidates = list(units_dir.glob("*.json"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or payload.get("source_identity") != source_identity:
                continue
            unit_name = payload.get("unit")
            if isinstance(unit_name, str):
                sibling_payloads.setdefault(unit_name, []).append(payload)

    for unit in source_units:
        name = str(unit["unit"])
        status = str(unit.get("status", "PENDING"))
        current_path = current_root / "units" / (str(unit["execution_unit_id"]).removeprefix("sha256:") + ".json")
        if status == "PASS":
            unit["resume_state"] = "PROVEN"
            proven.append(name)
            continue
        reason: str | None = None
        if current_path.exists():
            reason = "current-evidence-invalid"
        elif sibling_payloads.get(name):
            reason = "verification-inputs-changed"
        if reason is not None:
            unit["resume_state"] = "INVALIDATED"
            unit["resume_invalidation_reason"] = reason
            invalidated.append({"unit": name, "reason": reason})
        elif status == "BLOCKED":
            unit["resume_state"] = "BLOCKED"
            blocked.append(name)
        else:
            unit["resume_state"] = "PENDING"
            pending.append(name)

    return {
        "proven": proven,
        "invalidated": invalidated,
        "pending": pending,
        "blocked": blocked,
        "failed": failed,
    }
