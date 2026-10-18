from __future__ import annotations

import shutil

import hashlib
import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import package_source_checkpoint as subject


def _configure_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    root = tmp_path / "source"
    root.mkdir()
    (root / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    output_dir = root / "release-artifacts"
    monkeypatch.setattr(subject, "ROOT", root)
    monkeypatch.setattr(subject, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(subject, "ARCHIVE", output_dir / "source.zip")
    monkeypatch.setattr(subject, "SHA_FILE", output_dir / "source.zip.sha256")
    monkeypatch.setattr(subject, "SHA_OUTPUT", output_dir / "archive-sha256.txt")
    monkeypatch.setattr(subject, "MANIFEST", output_dir / "source-manifest.json")
    return root, output_dir


def _admit_fixture(
    monkeypatch: pytest.MonkeyPatch, *, identity: str = "sha256:" + "1" * 64
) -> None:
    monkeypatch.setattr(subject, "require_source_seal_gate", lambda _root: None)
    monkeypatch.setattr(
        subject, "_require_packaged_architecture_fresh", lambda _archive: None
    )
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)
    monkeypatch.setattr(
        subject, "_dependency_transport", lambda _root: {"schema": "test"}
    )


def test_release_source_package_publishes_sealed_artifact_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    _admit_fixture(monkeypatch)
    payload = b"persistent archive bytes"
    digest = hashlib.sha256(payload).hexdigest()

    def fake_package(output: Path) -> str:
        output.write_bytes(payload)
        return digest

    monkeypatch.setattr(subject, "package_slim", fake_package)

    observed = subject.package_checkpoint()

    assert observed == digest
    assert (output_dir / "source.zip").read_bytes() == payload
    assert (output_dir / "source.zip.sha256").read_text(
        encoding="utf-8"
    ) == f"{digest}  source.zip\n"
    manifest = json.loads(
        (output_dir / "source-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "artifact": "source.zip",
        "byte_count": len(payload),
        "dependency_transport": {"schema": "test"},
        "seal_gate": {
            "canonical_goon_static_admission": "PASS",
            "generated_contracts": "PASS",
            "packaged_architecture": "PASS",
            "source_architecture": "PASS",
        },
        "sha256": digest,
        "source_identity": "sha256:" + "1" * 64,
        "version": "7.8.9",
    }


def test_release_source_package_requires_source_and_packaged_seal_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_root(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        subject, "require_source_seal_gate", lambda _root: calls.append("source")
    )
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:" + "2" * 64
    )
    monkeypatch.setattr(
        subject, "_dependency_transport", lambda _root: {"schema": "test"}
    )

    def fake_package(output: Path) -> str:
        output.write_bytes(b"bytes")
        return hashlib.sha256(b"bytes").hexdigest()

    monkeypatch.setattr(subject, "package_slim", fake_package)
    monkeypatch.setattr(
        subject, "archive_source_identity", lambda _archive: "sha256:" + "2" * 64
    )
    monkeypatch.setattr(
        subject,
        "_require_packaged_architecture_fresh",
        lambda _archive: calls.append("packaged"),
    )

    subject.package_checkpoint()

    assert calls == ["source", "packaged"]


def test_release_source_package_refuses_source_identity_change_after_packaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    monkeypatch.setattr(subject, "require_source_seal_gate", lambda _root: None)
    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: "sha256:" + "3" * 64
    )
    monkeypatch.setattr(
        subject, "_dependency_transport", lambda _root: {"schema": "test"}
    )

    def fake_package(output: Path) -> str:
        output.write_bytes(b"bytes")
        return hashlib.sha256(b"bytes").hexdigest()

    monkeypatch.setattr(subject, "package_slim", fake_package)
    monkeypatch.setattr(
        subject, "archive_source_identity", lambda _archive: "sha256:" + "4" * 64
    )

    with pytest.raises(RuntimeError, match="packaged source identity differs"):
        subject.package_checkpoint()

    assert not output_dir.exists()


def test_release_source_package_refuses_failed_source_seal_before_byte_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    calls: list[str] = []

    def reject(_root: Path) -> None:
        raise RuntimeError("architecture red")

    monkeypatch.setattr(subject, "require_source_seal_gate", reject)
    monkeypatch.setattr(
        subject, "package_slim", lambda _output: calls.append("package")
    )

    with pytest.raises(RuntimeError, match="architecture red"):
        subject.package_checkpoint()

    assert calls == []
    assert not output_dir.exists()


def test_release_source_package_refuses_failed_packaged_architecture_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    identity = "sha256:" + "5" * 64
    monkeypatch.setattr(subject, "require_source_seal_gate", lambda _root: None)
    monkeypatch.setattr(subject, "canonical_source_identity", lambda _root: identity)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: identity)
    monkeypatch.setattr(
        subject, "_dependency_transport", lambda _root: {"schema": "test"}
    )

    def fake_package(output: Path) -> str:
        output.write_bytes(b"bytes")
        return hashlib.sha256(b"bytes").hexdigest()

    def reject(_archive: Path) -> None:
        raise RuntimeError("packaged architecture red")

    monkeypatch.setattr(subject, "package_slim", fake_package)
    monkeypatch.setattr(subject, "_require_packaged_architecture_fresh", reject)

    with pytest.raises(RuntimeError, match="packaged architecture red"):
        subject.package_checkpoint()

    assert not output_dir.exists()


def test_dependency_transport_manifest_declares_committed_uv_lock_authority(
    tmp_path: Path,
) -> None:
    backend_lock = tmp_path / "backend/uv.lock"
    backend_lock.parent.mkdir(parents=True)
    backend_lock.write_text(
        'version = 1\nrequires-python = ">=3.12"\npackage = []\n', encoding="utf-8"
    )
    frontend_lock = tmp_path / "frontend/package-lock.json"
    frontend_lock.parent.mkdir(parents=True)
    frontend_lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")

    manifest = subject._dependency_transport(tmp_path)

    assert manifest["backend"] == {
        "intent": "backend/pyproject.toml",
        "native_authority": "backend/uv.lock",
        "native_authority_sha256": hashlib.sha256(
            backend_lock.read_bytes()
        ).hexdigest(),
        "native_lock_transported": True,
        "consumer_mode": "uv --frozen",
        "cold_host_materialization": "make init",
        "authoring": "make sync",
    }
    assert isinstance(manifest["frontend"], dict)
    assert manifest["frontend"]["portable_authority"] == "frontend/package-lock.json"
    assert manifest["frontend"]["native_npm_cache_transported"] is False
    assert manifest["native_package_manager_caches_transported"] is False


def test_release_source_package_refuses_published_archive_byte_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    _admit_fixture(monkeypatch)
    payload = b"sealed archive bytes"
    digest = hashlib.sha256(payload).hexdigest()

    def fake_package(output: Path) -> str:
        output.write_bytes(payload)
        return digest

    real_copyfile = shutil.copyfile

    def corrupt_promoted_bytes(source: Path, destination: Path) -> Path:
        result = real_copyfile(source, destination)
        destination.write_bytes(b"corrupted during promotion")
        return result

    monkeypatch.setattr(subject, "package_slim", fake_package)
    monkeypatch.setattr(shutil, "copyfile", corrupt_promoted_bytes)

    with pytest.raises(RuntimeError, match="published source digest differs"):
        subject.package_checkpoint()

    assert not (output_dir / "source.zip.sha256").exists()
    assert not (output_dir / "archive-sha256.txt").exists()
    assert not (output_dir / "source-manifest.json").exists()


def test_release_source_package_binds_seal_gate_to_exact_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, output_dir = _configure_root(tmp_path, monkeypatch)
    before = "sha256:" + "6" * 64
    after = "sha256:" + "7" * 64
    state = {"identity": before}
    package_calls: list[Path] = []

    monkeypatch.setattr(
        subject, "canonical_source_identity", lambda _root: state["identity"]
    )

    def mutate_during_gate(_root: Path) -> None:
        state["identity"] = after

    def fake_package(output: Path) -> str:
        package_calls.append(output)
        output.write_bytes(b"mutated bytes")
        return hashlib.sha256(b"mutated bytes").hexdigest()

    monkeypatch.setattr(subject, "require_source_seal_gate", mutate_during_gate)
    monkeypatch.setattr(subject, "package_slim", fake_package)
    monkeypatch.setattr(subject, "archive_source_identity", lambda _archive: after)
    monkeypatch.setattr(
        subject, "_require_packaged_architecture_fresh", lambda _archive: None
    )
    monkeypatch.setattr(
        subject, "_dependency_transport", lambda _root: {"schema": "test"}
    )

    with pytest.raises(
        RuntimeError, match="source identity changed while the source seal gate ran"
    ):
        subject.package_checkpoint()

    assert package_calls == []
    assert not output_dir.exists()
