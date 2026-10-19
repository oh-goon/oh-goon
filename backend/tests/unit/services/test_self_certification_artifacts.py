from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from oh_goon.repository_tooling import self_certification_artifacts as artifacts


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_zip(source: Path, output: Path) -> str:
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    prefix = f"oh-goon-{version}/"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(prefix + relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.date_time = (2020, 2, 2, 0, 0, 0)
            info.external_attr = (0o100644) << 16
            archive.writestr(info, path.read_bytes())
    return _sha(output)


def test_fresh_source_zip_rejects_missing_committed_uv_lock_before_native_bootstrap(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "root"
    (root / "backend").mkdir(parents=True)
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (root / "backend/pyproject.toml").write_text(
        '[project]\nname="demo"\nversion="1.2.3"\nrequires-python=">=3.12,<3.15"\n'
        "[dependency-groups]\ndev=[]\n",
        encoding="utf-8",
    )
    archive = tmp_path / "oh-goon-1.2.3.zip"
    digest = _canonical_zip(root, archive)
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    from oh_goon.repository_tooling import _self_certification_artifact_verification as verification

    monkeypatch.setattr(verification, "archive_source_identity", lambda _path: "sha256:source")
    state = tmp_path / "state"
    try:
        artifacts.verify_fresh_source_zip(
            root, state, archive, digest, "sha256:source", "sha256:seal"
        )
    except RuntimeError as exc:
        assert "dependency authorities are missing or unreadable" in str(exc)
    else:
        raise AssertionError("missing committed backend lock must fail before native bootstrap")


def test_cumulative_patch_supports_binary_changes_and_replays_exact_bytes(
    tmp_path, monkeypatch
) -> None:
    parent_root = tmp_path / "parent"
    current_root = tmp_path / "current"
    parent_root.mkdir()
    current_root.mkdir()
    (parent_root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (current_root / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    (parent_root / "blob.bin").write_bytes(b"\x00old-binary\xff")
    (current_root / "blob.bin").write_bytes(b"\x00new-binary\xfe")
    parent_zip = tmp_path / "oh-goon-1.0.0.zip"
    current_zip = tmp_path / "oh-goon-1.0.1.zip"
    parent_sha = _canonical_zip(parent_root, parent_zip)
    current_sha = _canonical_zip(current_root, current_zip)
    spec = {
        "schema": 1,
        "version": "1.0.0",
        "artifact": parent_zip.name,
        "sha256": parent_sha,
    }
    monkeypatch.setattr(artifacts, "parent_checkpoint_spec", lambda _root: spec)
    state_dir = tmp_path / "state" / "source" / "config"
    parent_evidence_path = artifacts.stage_parent_source_zip(current_root, state_dir, parent_zip)
    assert parent_evidence_path.is_file()
    parent_evidence = artifacts.load_parent_source_input(current_root, state_dir)
    assert parent_evidence is not None
    patch = artifacts.build_cumulative_patch(
        current_root,
        state_dir,
        current_zip,
        current_sha,
        parent_evidence,
    )
    assert b"GIT binary patch" in patch.read_bytes()
    patch_evidence = artifacts.load_cumulative_patch_evidence(
        current_root,
        state_dir,
        current_sha,
        parent_evidence,
    )
    assert patch_evidence is not None

    def fake_package_slim(output: Path, *, root: Path | None = None) -> str:
        assert root is not None
        return _canonical_zip(root, output)

    monkeypatch.setattr(artifacts, "package_slim", fake_package_slim)
    artifacts.verify_cumulative_patch_replay(
        current_root,
        state_dir,
        current_zip,
        current_sha,
        patch_evidence,
    )
    replay = artifacts.load_patch_replay_evidence(state_dir, current_sha, patch_evidence)
    assert replay is not None
    assert replay["status"] == "BYTE_IDENTICAL"


def test_retained_readback_evidence_is_invalidated_by_later_byte_corruption(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    state_dir = tmp_path / "state" / "self-certification" / "source" / "config"
    artifact_set_identity = "sha256:" + "1" * 64
    retained = artifacts.retained_artifact_directory(state_dir, artifact_set_identity)
    retained.mkdir(parents=True)
    payload_file = retained / "payload.bin"
    payload_file.write_bytes(b"retained-bytes")
    manifest = retained / "oh-goon-1.0.0-artifacts.json"
    persistence_identity = artifacts._persistence_identity(artifact_set_identity)
    manifest_payload = {
        "artifact_set_identity": artifact_set_identity,
        "persistence": {
            "policy": "content-addressed-retained-artifact-set-v1",
            "identity": persistence_identity,
        },
        "artifacts": [
            {
                "name": payload_file.name,
                "sha256": _sha(payload_file),
                "byte_count": payload_file.stat().st_size,
            }
        ],
    }
    manifest.write_text(json.dumps(manifest_payload, sort_keys=True) + "\n", encoding="utf-8")
    manifest_sha = _sha(manifest)
    manifest.with_name(manifest.name + ".sha256").write_text(
        f"{manifest_sha}  {manifest.name}\n", encoding="utf-8"
    )
    manifest_evidence = {
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": persistence_identity,
        "manifest_sha256": manifest_sha,
    }
    persist_base = {
        "schema": 2,
        "status": "RETAINED",
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": persistence_identity,
        "manifest_sha256": manifest_sha,
        "file_count": 3,
    }
    persist_evidence = {
        **persist_base,
        "evidence_identity": artifacts._sha256_json(persist_base),
    }
    artifacts.verify_retained_artifact_set(
        root,
        state_dir,
        manifest_evidence,
        persist_evidence,
    )
    assert (
        artifacts.load_retained_readback_evidence(
            root,
            state_dir,
            manifest_evidence,
            persist_evidence,
        )
        is not None
    )
    payload_file.write_bytes(b"corrupted-after-verification")
    assert (
        artifacts.load_retained_readback_evidence(
            root,
            state_dir,
            manifest_evidence,
            persist_evidence,
        )
        is None
    )


def test_artifact_manifest_binds_version_package_replay_and_persistence_identity(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    spec = {
        "schema": 1,
        "version": "1.2.2",
        "artifact": "oh-goon-1.2.2.zip",
        "sha256": "a" * 64,
    }
    monkeypatch.setattr(artifacts, "parent_checkpoint_spec", lambda _root: spec)
    state_dir = tmp_path / "state" / "self-certification" / "source" / "config"
    artifact_dir = artifacts.artifact_directory(state_dir)
    artifact_dir.mkdir(parents=True)
    source_zip = artifacts.source_zip_path(root, state_dir)
    patch = artifacts.cumulative_patch_path(root, state_dir)
    handoff = artifacts.handoff_path(root, state_dir)
    for path, payload in ((source_zip, b"zip"), (patch, b"patch"), (handoff, b"handoff")):
        path.write_bytes(payload)
        digest = _sha(path)
        path.with_name(path.name + ".sha256").write_text(
            f"{digest}  {path.name}\n", encoding="utf-8"
        )
    sidecars = {"evidence_identity": "sha256:" + "1" * 64}
    replay = {"status": "BYTE_IDENTICAL", "evidence_identity": "sha256:" + "2" * 64}
    fresh = {"status": "PASS", "evidence_identity": "sha256:" + "3" * 64}
    package_config_identity = "sha256:" + "4" * 64
    manifest = artifacts.build_artifact_manifest(
        root,
        state_dir,
        plan_identity="sha256:" + "5" * 64,
        source_identity="sha256:" + "6" * 64,
        verification_config_identity="sha256:" + "7" * 64,
        seal_identity="sha256:" + "8" * 64,
        package_config_identity=package_config_identity,
        sidecar_evidence=sidecars,
        replay_evidence=replay,
        fresh_zip_evidence=fresh,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema"] == 2
    assert payload["product_version"] == "1.2.3"
    assert payload["package_config_identity"] == package_config_identity
    assert payload["parent"] == spec
    assert payload["patch_replay"] == replay
    assert payload["fresh_package_acceptance"] == fresh
    assert payload["persistence"]["identity"] == artifacts._persistence_identity(
        payload["artifact_set_identity"]
    )
    evidence = artifacts.load_manifest_evidence(
        root, state_dir, package_config_identity, sidecars, replay, fresh
    )
    assert evidence is not None
    assert evidence["persistence_identity"] == payload["persistence"]["identity"]

    assert (
        artifacts.load_manifest_evidence(
            root, state_dir, "sha256:" + "9" * 64, sidecars, replay, fresh
        )
        is None
    )
    changed_replay = {**replay, "status": "FAILED"}
    assert (
        artifacts.load_manifest_evidence(
            root, state_dir, package_config_identity, sidecars, changed_replay, fresh
        )
        is None
    )


def test_persistence_rejects_manifest_persistence_identity_drift(tmp_path, monkeypatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    state_dir = tmp_path / "state" / "self-certification" / "source" / "config"
    artifact_dir = artifacts.artifact_directory(state_dir)
    artifact_dir.mkdir(parents=True)
    manifest = artifacts.manifest_path(root, state_dir)
    artifact_set_identity = "sha256:" + "a" * 64
    payload = {
        "artifact_set_identity": artifact_set_identity,
        "persistence": {
            "policy": "content-addressed-retained-artifact-set-v1",
            "identity": "sha256:" + "b" * 64,
        },
        "artifacts": [],
    }
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    digest = _sha(manifest)
    manifest.with_name(manifest.name + ".sha256").write_text(
        f"{digest}  {manifest.name}\n", encoding="utf-8"
    )
    evidence = {
        "artifact_set_identity": artifact_set_identity,
        "persistence_identity": "sha256:" + "c" * 64,
        "manifest_sha256": digest,
    }
    try:
        artifacts.persist_artifact_set(root, state_dir, evidence)
    except RuntimeError as exc:
        assert "persistence identity mismatch" in str(exc)
    else:
        raise AssertionError("persistence drift must fail closed")


def test_checkpoint_parent_binding_derives_version_and_hash_from_exact_zip(tmp_path) -> None:
    from oh_goon.repository_tooling.release.checkpoint_parent import bind_checkpoint_parent

    root = tmp_path / "root"
    parent_root = tmp_path / "parent"
    root.mkdir()
    parent_root.mkdir()
    (root / "generated/release").mkdir(parents=True)
    (parent_root / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    (parent_root / "payload.txt").write_text("parent\n", encoding="utf-8")
    parent_zip = tmp_path / "arbitrary-persisted-parent-name.zip"
    parent_sha = _canonical_zip(parent_root, parent_zip)

    payload = bind_checkpoint_parent(root, parent_zip)

    assert payload == {
        "schema": 1,
        "version": "7.8.9",
        "artifact": "oh-goon-7.8.9.zip",
        "sha256": parent_sha,
    }
    assert artifacts.parent_checkpoint_spec(root) == payload


def test_checkpoint_parent_setup_refuses_absent_local_archive_metadata(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="checkpoint archive setup requires local parent metadata"):
        artifacts.parent_checkpoint_spec(tmp_path)
