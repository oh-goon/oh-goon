from __future__ import annotations

from pathlib import Path
from typing import Iterable

from oh_goon.repository_tooling._self_certification_artifact_verification import (
    _atomic_write,
    _load_evidence,
    _record_evidence,
    _sha256_file,
)


def build_handoff_report(
    *,
    state_dir: Path,
    output: Path,
    version: str,
    parent_spec: dict[str, object],
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
    units = list(source_units)
    if any(unit.get("status") != "PASS" for unit in units):
        raise RuntimeError("handoff requires all source units to have current PASS evidence")
    lines = [
        f"OH-GOON {version} HANDOFF",
        "=" * (15 + len(version)),
        "",
        "Checkpoint",
        "----------",
        f"Version: {version}",
        "Status: BLOCKED only pending the authoritative dependency-complete WSL startup/certification proof on these exact retained bytes.",
        "",
        "Canonical self-certification evidence",
        "-------------------------------------",
        f"Plan identity: {plan_identity}",
        f"Source identity: {source_identity}",
        f"Verification config identity: {verification_config_identity}",
        f"Source seal identity: {seal_identity}",
        f"Bounded source units PASS: {len(units)}/{len(units)}",
    ]
    for unit in units:
        lines.append(f"- {unit['unit']}: {unit.get('evidence_identity')}")
    lines += [
        "",
        "Artifact proof",
        "--------------",
        f"Parent ZIP: {parent_spec['artifact']}",
        f"Parent ZIP SHA-256: {parent_spec['sha256']}",
        f"Canonical ZIP: oh-goon-{version}.zip",
        f"Canonical ZIP SHA-256: {source_zip_sha256}",
        f"Cumulative patch: oh-goon-{version}.patch",
        f"Patch SHA-256: {patch_evidence['artifact_sha256']}",
        f"Patch replay: {replay_evidence['status']}",
        f"Fresh ZIP acceptance: {fresh_zip_evidence['status']}",
        "",
        "Execution contract",
        "------------------",
        "The source was verified once as bounded identity-bound units, then sealed.",
        "Post-seal artifact work did not rerun source-wide verification.",
        "Interrupted tool sessions resume from current evidence rather than restarting completed units.",
        "",
        "Still not claimed by this artifact Plan",
        "---------------------------------------",
        "- Full APP make certify on these exact bytes.",
        "- Release status. This remains a checkpoint until the authoritative startup/certification proof passes.",
        "",
    ]
    _atomic_write(output, ("\n".join(lines)).encode("utf-8"))
    base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "artifact": output.name,
        "artifact_sha256": _sha256_file(output),
        "source_zip_sha256": source_zip_sha256,
        "patch_evidence_identity": patch_evidence["evidence_identity"],
        "patch_replay_evidence_identity": replay_evidence["evidence_identity"],
        "fresh_zip_evidence_identity": fresh_zip_evidence["evidence_identity"],
    }
    _record_evidence(state_dir / "package-handoff.json", base)
    return output


def load_handoff_report_evidence(
    *,
    state_dir: Path,
    handoff: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    replay_evidence: dict[str, object],
    fresh_zip_evidence: dict[str, object],
) -> dict[str, object] | None:
    evidence = _load_evidence(
        state_dir / "package-handoff.json",
        {
            "status": "PASS",
            "source_zip_sha256": source_zip_sha256,
            "patch_evidence_identity": patch_evidence["evidence_identity"],
            "patch_replay_evidence_identity": replay_evidence["evidence_identity"],
            "fresh_zip_evidence_identity": fresh_zip_evidence["evidence_identity"],
        },
    )
    if evidence is None:
        return None
    try:
        digest = _sha256_file(handoff)
    except OSError:
        return None
    return evidence if evidence.get("artifact_sha256") == digest else None


def _write_sidecar(path: Path) -> Path:
    digest = _sha256_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    return _atomic_write(sidecar, f"{digest}  {path.name}\n".encode("utf-8"))


def build_artifact_sidecars_report(
    *,
    state_dir: Path,
    source_zip: Path,
    patch: Path,
    handoff: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    handoff_evidence: dict[str, object],
) -> Path:
    if _sha256_file(source_zip) != source_zip_sha256:
        raise RuntimeError("source ZIP changed before sidecar finalization")
    if _sha256_file(patch) != patch_evidence["artifact_sha256"]:
        raise RuntimeError("cumulative patch changed before sidecar finalization")
    if _sha256_file(handoff) != handoff_evidence["artifact_sha256"]:
        raise RuntimeError("handoff changed before sidecar finalization")
    for path in (source_zip, patch, handoff):
        _write_sidecar(path)
    base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "source_zip_sha256": source_zip_sha256,
        "patch_sha256": patch_evidence["artifact_sha256"],
        "handoff_sha256": handoff_evidence["artifact_sha256"],
    }
    return _record_evidence(state_dir / "package-sidecars.json", base)


def load_sidecar_report_evidence(
    *,
    state_dir: Path,
    source_zip: Path,
    patch: Path,
    handoff: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    handoff_evidence: dict[str, object],
) -> dict[str, object] | None:
    evidence = _load_evidence(
        state_dir / "package-sidecars.json",
        {
            "status": "PASS",
            "source_zip_sha256": source_zip_sha256,
            "patch_sha256": patch_evidence["artifact_sha256"],
            "handoff_sha256": handoff_evidence["artifact_sha256"],
        },
    )
    if evidence is None:
        return None
    expected_hashes = {
        source_zip: source_zip_sha256,
        patch: str(patch_evidence["artifact_sha256"]),
        handoff: str(handoff_evidence["artifact_sha256"]),
    }
    for path, expected_hash in expected_hashes.items():
        sidecar = path.with_name(path.name + ".sha256")
        try:
            observed = _sha256_file(path)
            if observed != expected_hash:
                return None
            expected = f"{observed}  {path.name}\n"
            if sidecar.read_text(encoding="utf-8") != expected:
                return None
        except OSError:
            return None
    return evidence
