from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from oh_goon.repository_tooling.release.package_slim import archive_source_identity, package_slim
from oh_goon.repository_tooling._self_certification_artifact_retention import (
    load_persist_evidence_impl as _load_persist_evidence,
    load_retained_readback_evidence_impl as _load_retained_readback_evidence,
    persist_artifact_set_impl as _persist_artifact_set,
    retained_artifact_directory as _retained_artifact_directory,
    verify_retained_artifact_set_impl as _verify_retained_artifact_set,
)
from oh_goon.repository_tooling._self_certification_artifact_reporting import (
    build_artifact_sidecars_report as _build_artifact_sidecars_report,
    build_handoff_report as _build_handoff_report,
    load_handoff_report_evidence as _load_handoff_report_evidence,
    load_sidecar_report_evidence as _load_sidecar_report_evidence,
    _write_sidecar,
)
from oh_goon.repository_tooling._self_certification_artifact_verification import (
    _archive_version,
    _atomic_write,
    _extract_source_zip,
    _load_evidence,
    _normalize_tree_times,
    _record_evidence,
    _sha256_file,
    _sha256_json,
    load_fresh_zip_evidence,
    load_patch_replay_evidence,
    verify_cumulative_patch_replay_impl,
    verify_fresh_source_zip,
)

PARENT_METADATA = Path("generated/release/checkpoint-parent.json")


def parent_checkpoint_spec(root: Path) -> dict[str, object]:
    path = root / PARENT_METADATA
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"checkpoint archive setup requires local parent metadata: {path}; "
            "restore the verified checkpoint handoff. "
            "Ordinary source development uses make init."
        ) from exc
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid checkpoint parent metadata: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("checkpoint parent metadata must use schema 1")
    version = payload.get("version")
    artifact = payload.get("artifact")
    digest = payload.get("sha256")
    if not isinstance(version, str) or not version:
        raise ValueError("checkpoint parent version is missing")
    if artifact != f"oh-goon-{version}.zip":
        raise ValueError("checkpoint parent artifact name must match its version")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("checkpoint parent sha256 must be a lowercase SHA-256 digest")
    return {"schema": 1, "version": version, "artifact": artifact, "sha256": digest}


def artifact_directory(state_dir: Path) -> Path:
    return state_dir / "artifacts"


def source_zip_path(root: Path, state_dir: Path) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return artifact_directory(state_dir) / f"oh-goon-{version}.zip"


def cumulative_patch_path(root: Path, state_dir: Path) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return artifact_directory(state_dir) / f"oh-goon-{version}.patch"


def handoff_path(root: Path, state_dir: Path) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return artifact_directory(state_dir) / f"oh-goon-{version}-handoff.txt"


def manifest_path(root: Path, state_dir: Path) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return artifact_directory(state_dir) / f"oh-goon-{version}-artifacts.json"


def parent_input_path(root: Path, state_dir: Path) -> Path:
    return state_dir / "inputs" / str(parent_checkpoint_spec(root)["artifact"])


def stage_parent_source_zip(root: Path, state_dir: Path, candidate: Path) -> Path:
    spec = parent_checkpoint_spec(root)
    candidate = candidate.expanduser().resolve()
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("parent source ZIP must be a regular non-symlink file")
    observed = _sha256_file(candidate)
    if observed != spec["sha256"]:
        raise ValueError(
            f"parent source ZIP SHA-256 mismatch: expected={spec['sha256']} observed={observed}"
        )
    if _archive_version(candidate) != spec["version"]:
        raise ValueError("parent source ZIP VERSION does not match checkpoint parent metadata")
    destination = parent_input_path(root, state_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(candidate, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    base: dict[str, object] = {
        "schema": 1,
        "status": "VERIFIED",
        "artifact": spec["artifact"],
        "version": spec["version"],
        "sha256": spec["sha256"],
        "byte_count": destination.stat().st_size,
    }
    return _record_evidence(state_dir / "parent-source-input.json", base)


def load_parent_source_input(root: Path, state_dir: Path) -> dict[str, object] | None:
    spec = parent_checkpoint_spec(root)
    evidence = _load_evidence(
        state_dir / "parent-source-input.json",
        {
            "status": "VERIFIED",
            "artifact": spec["artifact"],
            "version": spec["version"],
            "sha256": spec["sha256"],
        },
    )
    if evidence is None:
        return None
    path = parent_input_path(root, state_dir)
    try:
        observed = _sha256_file(path)
    except OSError:
        return None
    if observed != spec["sha256"] or evidence.get("byte_count") != path.stat().st_size:
        return None
    try:
        version = _archive_version(path)
    except (OSError, ValueError):
        return None
    return evidence if version == spec["version"] else None


def build_cumulative_patch(
    root: Path,
    state_dir: Path,
    source_zip: Path,
    source_zip_sha256: str,
    parent_input: dict[str, object],
) -> Path:
    spec = parent_checkpoint_spec(root)
    if parent_input.get("sha256") != spec["sha256"]:
        raise RuntimeError("current parent input evidence is unavailable")
    if _sha256_file(source_zip) != source_zip_sha256:
        raise RuntimeError("source ZIP changed before cumulative patch creation")
    output = cumulative_patch_path(root, state_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oh-goon-cumulative-patch-", dir=output.parent) as directory:
        workspace = Path(directory)
        before = workspace / "a"
        after = workspace / "b"
        _extract_source_zip(parent_input_path(root, state_dir), before)
        _extract_source_zip(source_zip, after)
        _normalize_tree_times(before)
        _normalize_tree_times(after)
        env = dict(os.environ)
        env.update({"LC_ALL": "C", "TZ": "UTC"})
        try:
            result = subprocess.run(
                ["git", "diff", "--no-index", "--binary", "--full-index", "--", "a", "b"],
                cwd=workspace,
                check=False,
                capture_output=True,
                env=env,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("cumulative patch generation exceeded its 120s execution budget") from exc
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f"cumulative git diff failed with exit {result.returncode}: "
                + result.stderr.decode("utf-8", errors="replace")[:400]
            )
        _atomic_write(output, result.stdout)
    digest = _sha256_file(output)
    base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "parent_sha256": spec["sha256"],
        "source_zip_sha256": source_zip_sha256,
        "artifact": output.name,
        "artifact_sha256": digest,
        "byte_count": output.stat().st_size,
    }
    _record_evidence(state_dir / "package-cumulative-patch.json", base)
    return output


def load_cumulative_patch_evidence(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    parent_input: dict[str, object],
) -> dict[str, object] | None:
    spec = parent_checkpoint_spec(root)
    if parent_input.get("sha256") != spec["sha256"]:
        return None
    evidence = _load_evidence(
        state_dir / "package-cumulative-patch.json",
        {
            "status": "PASS",
            "parent_sha256": spec["sha256"],
            "source_zip_sha256": source_zip_sha256,
        },
    )
    if evidence is None:
        return None
    path = cumulative_patch_path(root, state_dir)
    try:
        digest = _sha256_file(path)
    except OSError:
        return None
    return evidence if evidence.get("artifact_sha256") == digest else None


def verify_cumulative_patch_replay(
    root: Path,
    state_dir: Path,
    source_zip: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
) -> Path:
    return verify_cumulative_patch_replay_impl(
        state_dir,
        source_zip,
        source_zip_sha256,
        patch_evidence,
        patch_path=cumulative_patch_path(root, state_dir),
        parent_input=parent_input_path(root, state_dir),
        artifact_dir=artifact_directory(state_dir),
        package_slim_fn=package_slim,
    )


def build_handoff(
    root: Path,
    state_dir: Path,
    *,
    plan_identity: str,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    source_units: Iterable[dict[str, object]],
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    replay_evidence: dict[str, object],
    fresh_zip_evidence: dict[str, object],
) -> Path:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    return _build_handoff_report(
        state_dir=state_dir,
        output=handoff_path(root, state_dir),
        version=version,
        parent_spec=parent_checkpoint_spec(root),
        plan_identity=plan_identity,
        source_identity=source_identity,
        verification_config_identity=verification_config_identity,
        seal_identity=seal_identity,
        source_units=source_units,
        source_zip_sha256=source_zip_sha256,
        patch_evidence=patch_evidence,
        replay_evidence=replay_evidence,
        fresh_zip_evidence=fresh_zip_evidence,
    )


def load_handoff_evidence(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    replay_evidence: dict[str, object],
    fresh_zip_evidence: dict[str, object],
) -> dict[str, object] | None:
    return _load_handoff_report_evidence(
        state_dir=state_dir,
        handoff=handoff_path(root, state_dir),
        source_zip_sha256=source_zip_sha256,
        patch_evidence=patch_evidence,
        replay_evidence=replay_evidence,
        fresh_zip_evidence=fresh_zip_evidence,
    )


def build_artifact_sidecars(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    handoff_evidence: dict[str, object],
) -> Path:
    return _build_artifact_sidecars_report(
        state_dir=state_dir,
        source_zip=source_zip_path(root, state_dir),
        patch=cumulative_patch_path(root, state_dir),
        handoff=handoff_path(root, state_dir),
        source_zip_sha256=source_zip_sha256,
        patch_evidence=patch_evidence,
        handoff_evidence=handoff_evidence,
    )


def load_sidecar_evidence(
    root: Path,
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    handoff_evidence: dict[str, object],
) -> dict[str, object] | None:
    return _load_sidecar_report_evidence(
        state_dir=state_dir,
        source_zip=source_zip_path(root, state_dir),
        patch=cumulative_patch_path(root, state_dir),
        handoff=handoff_path(root, state_dir),
        source_zip_sha256=source_zip_sha256,
        patch_evidence=patch_evidence,
        handoff_evidence=handoff_evidence,
    )


def _artifact_rows(paths: Iterable[Path]) -> list[dict[str, object]]:
    return [
        {"name": path.name, "sha256": _sha256_file(path), "byte_count": path.stat().st_size}
        for path in paths
    ]


def _persistence_identity(artifact_set_identity: str) -> str:
    return _sha256_json(
        {
            "policy": "content-addressed-retained-artifact-set-v1",
            "artifact_set_identity": artifact_set_identity,
        }
    )


def build_artifact_manifest(
    root: Path,
    state_dir: Path,
    *,
    plan_identity: str,
    source_identity: str,
    verification_config_identity: str,
    seal_identity: str,
    package_config_identity: str,
    sidecar_evidence: dict[str, object],
    replay_evidence: dict[str, object],
    fresh_zip_evidence: dict[str, object],
) -> Path:
    spec = parent_checkpoint_spec(root)
    source_zip = source_zip_path(root, state_dir)
    patch = cumulative_patch_path(root, state_dir)
    handoff = handoff_path(root, state_dir)
    artifacts = [
        source_zip,
        source_zip.with_name(source_zip.name + ".sha256"),
        patch,
        patch.with_name(patch.name + ".sha256"),
        handoff,
        handoff.with_name(handoff.name + ".sha256"),
    ]
    if not all(path.is_file() for path in artifacts):
        raise RuntimeError("artifact manifest requires the complete artifact/sidecar set")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    base: dict[str, object] = {
        "schema": 2,
        "product_version": version,
        "plan_identity": plan_identity,
        "source_identity": source_identity,
        "verification_config_identity": verification_config_identity,
        "seal_identity": seal_identity,
        "package_config_identity": package_config_identity,
        "parent": spec,
        "patch_replay": {
            "status": replay_evidence["status"],
            "evidence_identity": replay_evidence["evidence_identity"],
        },
        "fresh_package_acceptance": {
            "status": fresh_zip_evidence["status"],
            "evidence_identity": fresh_zip_evidence["evidence_identity"],
        },
        "sidecar_evidence_identity": sidecar_evidence["evidence_identity"],
        "artifacts": _artifact_rows(artifacts),
    }
    artifact_set_identity = _sha256_json(base)
    persistence_identity = _persistence_identity(artifact_set_identity)
    payload = {
        **base,
        "artifact_set_identity": artifact_set_identity,
        "persistence": {
            "policy": "content-addressed-retained-artifact-set-v1",
            "identity": persistence_identity,
        },
    }
    output = manifest_path(root, state_dir)
    _atomic_write(output, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    _write_sidecar(output)
    evidence_base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": persistence_identity,
        "product_version": version,
        "package_config_identity": package_config_identity,
        "manifest": output.name,
        "manifest_sha256": _sha256_file(output),
        "manifest_sidecar_sha256": _sha256_file(output.with_name(output.name + ".sha256")),
        "sidecar_evidence_identity": sidecar_evidence["evidence_identity"],
    }
    _record_evidence(state_dir / "package-artifact-manifest.json", evidence_base)
    return output


def load_manifest_evidence(
    root: Path,
    state_dir: Path,
    package_config_identity: str,
    sidecar_evidence: dict[str, object],
    replay_evidence: dict[str, object],
    fresh_zip_evidence: dict[str, object],
) -> dict[str, object] | None:
    evidence = _load_evidence(
        state_dir / "package-artifact-manifest.json",
        {
            "status": "PASS",
            "product_version": (root / "VERSION").read_text(encoding="utf-8").strip(),
            "package_config_identity": package_config_identity,
            "sidecar_evidence_identity": sidecar_evidence["evidence_identity"],
        },
    )
    if evidence is None:
        return None
    manifest = manifest_path(root, state_dir)
    sidecar = manifest.with_name(manifest.name + ".sha256")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("schema") != 2:
            return None
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        if payload.get("product_version") != version:
            return None
        if payload.get("package_config_identity") != package_config_identity:
            return None
        if payload.get("parent") != parent_checkpoint_spec(root):
            return None
        if payload.get("patch_replay") != {
            "status": replay_evidence.get("status"),
            "evidence_identity": replay_evidence.get("evidence_identity"),
        }:
            return None
        if payload.get("fresh_package_acceptance") != {
            "status": fresh_zip_evidence.get("status"),
            "evidence_identity": fresh_zip_evidence.get("evidence_identity"),
        }:
            return None
        base = dict(payload)
        artifact_set_identity = base.pop("artifact_set_identity", None)
        persistence = base.pop("persistence", None)
        if artifact_set_identity != _sha256_json(base):
            return None
        expected_persistence = {
            "policy": "content-addressed-retained-artifact-set-v1",
            "identity": _persistence_identity(str(artifact_set_identity)),
        }
        if persistence != expected_persistence:
            return None
        if evidence.get("persistence_identity") != expected_persistence["identity"]:
            return None
        if artifact_set_identity != evidence.get("artifact_set_identity"):
            return None
        if evidence.get("manifest_sha256") != _sha256_file(manifest):
            return None
        artifact_dir = artifact_directory(state_dir)
        for row in payload.get("artifacts", []):
            if not isinstance(row, dict):
                return None
            artifact = artifact_dir / str(row.get("name", ""))
            if not artifact.is_file() or artifact.is_symlink():
                return None
            if _sha256_file(artifact) != row.get("sha256") or artifact.stat().st_size != row.get("byte_count"):
                return None
        expected_sidecar = f"{_sha256_file(manifest)}  {manifest.name}\n"
        if sidecar.read_text(encoding="utf-8") != expected_sidecar:
            return None
    except (OSError, ValueError, TypeError):
        return None
    return evidence


def retained_artifact_directory(state_dir: Path, artifact_set_identity: str) -> Path:
    return _retained_artifact_directory(state_dir, artifact_set_identity)


def persist_artifact_set(root: Path, state_dir: Path, manifest_evidence: dict[str, object]) -> Path:
    return _persist_artifact_set(
        state_dir=state_dir,
        source_manifest=manifest_path(root, state_dir),
        source_dir=artifact_directory(state_dir),
        manifest_evidence=manifest_evidence,
    )


def load_persist_evidence(
    root: Path,
    state_dir: Path,
    manifest_evidence: dict[str, object],
) -> dict[str, object] | None:
    return _load_persist_evidence(state_dir=state_dir, manifest_evidence=manifest_evidence)


def verify_retained_artifact_set(
    root: Path,
    state_dir: Path,
    manifest_evidence: dict[str, object],
    persist_evidence: dict[str, object],
) -> Path:
    return _verify_retained_artifact_set(
        state_dir=state_dir,
        retained_manifest_name=manifest_path(root, state_dir).name,
        manifest_evidence=manifest_evidence,
        persist_evidence=persist_evidence,
    )


def load_retained_readback_evidence(
    root: Path,
    state_dir: Path,
    manifest_evidence: dict[str, object],
    persist_evidence: dict[str, object],
) -> dict[str, object] | None:
    return _load_retained_readback_evidence(
        state_dir=state_dir,
        retained_manifest_name=manifest_path(root, state_dir).name,
        manifest_evidence=manifest_evidence,
        persist_evidence=persist_evidence,
    )
