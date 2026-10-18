from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from oh_goon.repository_tooling._self_certification_artifact_verification import (
    _load_evidence,
    _record_evidence,
    _sha256_file,
)


def _retention_root(state_dir: Path) -> Path:
    if len(state_dir.parents) < 2:
        raise ValueError("self-certification state directory is not namespaced")
    return state_dir.parents[1] / "retained"


def retained_artifact_directory(state_dir: Path, artifact_set_identity: str) -> Path:
    return _retention_root(state_dir) / artifact_set_identity.removeprefix("sha256:")


def persist_artifact_set_impl(
    *,
    state_dir: Path,
    source_manifest: Path,
    source_dir: Path,
    manifest_evidence: dict[str, object],
) -> Path:
    artifact_set_identity = str(manifest_evidence["artifact_set_identity"])
    destination = retained_artifact_directory(state_dir, artifact_set_identity)
    source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    names = [str(row["name"]) for row in source_payload["artifacts"]]
    names += [source_manifest.name, source_manifest.name + ".sha256"]
    if destination.exists():
        if not destination.is_dir() or destination.is_symlink():
            raise RuntimeError("retained artifact-set destination is not a directory")
        for name in names:
            if _sha256_file(destination / name) != _sha256_file(source_dir / name):
                raise RuntimeError("existing retained artifact set differs from current manifest")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
        try:
            for name in names:
                shutil.copyfile(source_dir / name, temporary / name)
            temporary.replace(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    persistence_identity = str(manifest_evidence["persistence_identity"])
    if source_payload.get("persistence", {}).get("identity") != persistence_identity:
        raise RuntimeError("artifact manifest persistence identity mismatch")
    base: dict[str, object] = {
        "schema": 2,
        "status": "RETAINED",
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": persistence_identity,
        "manifest_sha256": manifest_evidence["manifest_sha256"],
        "file_count": len(names),
    }
    return _record_evidence(state_dir / "package-persist.json", base)


def load_persist_evidence_impl(
    *, state_dir: Path, manifest_evidence: dict[str, object]
) -> dict[str, object] | None:
    evidence = _load_evidence(
        state_dir / "package-persist.json",
        {
            "status": "RETAINED",
            "artifact_set_identity": manifest_evidence["artifact_set_identity"],
            "persistence_identity": manifest_evidence["persistence_identity"],
            "manifest_sha256": manifest_evidence["manifest_sha256"],
        },
    )
    if evidence is None:
        return None
    destination = retained_artifact_directory(
        state_dir, str(manifest_evidence["artifact_set_identity"])
    )
    return evidence if destination.is_dir() and not destination.is_symlink() else None


def verify_retained_artifact_set_impl(
    *,
    state_dir: Path,
    retained_manifest_name: str,
    manifest_evidence: dict[str, object],
    persist_evidence: dict[str, object],
) -> Path:
    artifact_set_identity = str(manifest_evidence["artifact_set_identity"])
    destination = retained_artifact_directory(state_dir, artifact_set_identity)
    retained_manifest = destination / retained_manifest_name
    payload = json.loads(retained_manifest.read_text(encoding="utf-8"))
    if payload.get("artifact_set_identity") != artifact_set_identity:
        raise RuntimeError("retained manifest artifact-set identity mismatch")
    persistence_identity = str(manifest_evidence["persistence_identity"])
    if payload.get("persistence", {}).get("identity") != persistence_identity:
        raise RuntimeError("retained manifest persistence identity mismatch")
    if persist_evidence.get("persistence_identity") != persistence_identity:
        raise RuntimeError("retained persistence evidence identity mismatch")
    for row in payload.get("artifacts", []):
        path = destination / str(row["name"])
        if _sha256_file(path) != row["sha256"] or path.stat().st_size != row["byte_count"]:
            raise RuntimeError(f"retained artifact read-back mismatch: {row['name']}")
    manifest_sha = _sha256_file(retained_manifest)
    if manifest_sha != manifest_evidence["manifest_sha256"]:
        raise RuntimeError("retained manifest bytes changed after persistence")
    sidecar = retained_manifest.with_name(retained_manifest.name + ".sha256")
    if sidecar.read_text(encoding="utf-8") != f"{manifest_sha}  {retained_manifest.name}\n":
        raise RuntimeError("retained manifest sidecar mismatch")
    base: dict[str, object] = {
        "schema": 1,
        "status": "VERIFIED",
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": persistence_identity,
        "persist_evidence_identity": persist_evidence["evidence_identity"],
        "manifest_sha256": manifest_sha,
    }
    return _record_evidence(state_dir / "package-retained-readback.json", base)


def load_retained_readback_evidence_impl(
    *,
    state_dir: Path,
    retained_manifest_name: str,
    manifest_evidence: dict[str, object],
    persist_evidence: dict[str, object],
) -> dict[str, object] | None:
    evidence = _load_evidence(
        state_dir / "package-retained-readback.json",
        {
            "status": "VERIFIED",
            "artifact_set_identity": manifest_evidence["artifact_set_identity"],
            "persistence_identity": manifest_evidence["persistence_identity"],
            "persist_evidence_identity": persist_evidence["evidence_identity"],
            "manifest_sha256": manifest_evidence["manifest_sha256"],
        },
    )
    if evidence is None:
        return None
    destination = retained_artifact_directory(
        state_dir, str(manifest_evidence["artifact_set_identity"])
    )
    retained_manifest = destination / retained_manifest_name
    try:
        payload = json.loads(retained_manifest.read_text(encoding="utf-8"))
        if payload.get("artifact_set_identity") != manifest_evidence["artifact_set_identity"]:
            return None
        if (
            payload.get("persistence", {}).get("identity")
            != manifest_evidence["persistence_identity"]
        ):
            return None
        for row in payload.get("artifacts", []):
            if not isinstance(row, dict):
                return None
            artifact = destination / str(row.get("name", ""))
            if not artifact.is_file() or artifact.is_symlink():
                return None
            if _sha256_file(artifact) != row.get("sha256") or artifact.stat().st_size != row.get(
                "byte_count"
            ):
                return None
        manifest_sha = _sha256_file(retained_manifest)
        if manifest_sha != manifest_evidence["manifest_sha256"]:
            return None
        sidecar = retained_manifest.with_name(retained_manifest.name + ".sha256")
        if sidecar.read_text(encoding="utf-8") != f"{manifest_sha}  {retained_manifest.name}\n":
            return None
    except (OSError, ValueError, TypeError):
        return None
    return evidence
