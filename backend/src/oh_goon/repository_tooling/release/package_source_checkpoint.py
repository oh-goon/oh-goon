from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from oh_goon.core.file_digest import sha256_file
from oh_goon.integrations.exact_file_write import (
    publish_text_file,
    replace_prepared_file,
    sync_prepared_path,
)
from oh_goon.repository_tooling.release.package_slim import (
    ROOT,
    archive_source_identity,
    canonical_source_identity,
    package_slim,
)
from oh_goon.repository_tooling.release.seal_gate import (
    require_source_seal_gate,
)

OUTPUT_DIR = ROOT / "release-artifacts"
ARCHIVE = OUTPUT_DIR / "source.zip"
SHA_FILE = OUTPUT_DIR / "source.zip.sha256"
SHA_OUTPUT = OUTPUT_DIR / "archive-sha256.txt"
MANIFEST = OUTPUT_DIR / "source-manifest.json"
_SEAL_GATE_MODULE = "oh_goon.repository_tooling.release.seal_gate"


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _require_packaged_architecture_fresh(archive_path: Path) -> None:
    """Replay packaged architecture in a fresh controller process.

    Source-side seal checks and package construction can populate process-local
    caches and imported-module state.  Packaged architecture is an independent
    authority cut and must therefore execute from a fresh controller against the
    exact ZIP bytes rather than inheriting the package producer's Python state.
    """

    result = subprocess.run(
        (
            sys.executable,
            "-m",
            _SEAL_GATE_MODULE,
            "--packaged-architecture",
            str(archive_path.resolve()),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "packaged architecture gate failed").strip()
    raise RuntimeError(f"checkpoint packaged architecture gate failed: {detail}")


def _dependency_transport(root: Path) -> dict[str, object]:
    backend_lock = root / "backend" / "uv.lock"
    frontend_lock = root / "frontend" / "package-lock.json"
    return {
        "schema": "oh-goon.dependency-transport/v2",
        "backend": {
            "intent": "backend/pyproject.toml",
            "native_authority": "backend/uv.lock",
            "native_authority_sha256": _sha256(backend_lock),
            "native_lock_transported": True,
            "consumer_mode": "uv --frozen",
            "cold_host_materialization": "make init",
            "authoring": "make sync",
        },
        "frontend": {
            "portable_authority": "frontend/package-lock.json",
            "portable_authority_sha256": _sha256(frontend_lock),
            "native_npm_cache_transported": False,
        },
        "native_package_manager_caches_transported": False,
    }


def _publish_archive_bytes(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    expected_source_identity: str,
) -> int:
    """Durably publish and re-prove the exact retained source bytes."""

    destination.parent.mkdir(mode=0o700)
    prepared = destination.with_name(f".{destination.name}.tmp")
    try:
        shutil.copyfile(source, prepared)
        sync_prepared_path(prepared)
        replace_prepared_file(prepared, destination)
    finally:
        prepared.unlink(missing_ok=True)

    published_digest = _sha256(destination)
    if published_digest != expected_digest:
        raise RuntimeError(
            "published source digest differs from the exact packaged bytes admitted before promotion"
        )
    published_source_identity = archive_source_identity(destination)
    if published_source_identity != expected_source_identity:
        raise RuntimeError(
            "published source identity differs from the exact packaged bytes admitted before promotion"
        )
    return destination.stat().st_size


def package_checkpoint() -> str:
    if OUTPUT_DIR.exists():
        raise RuntimeError(
            "release-artifacts already exists; run the release source cleanup Goon before packaging"
        )
    source_identity = canonical_source_identity(ROOT)
    require_source_seal_gate(ROOT)
    if canonical_source_identity(ROOT) != source_identity:
        raise RuntimeError(
            "source identity changed while the source seal gate ran; refusing to package unadmitted bytes"
        )
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    dependency_transport = _dependency_transport(ROOT)
    with tempfile.TemporaryDirectory(prefix="oh-goon-release-package-") as directory:
        temporary_archive = Path(directory) / "source.zip"
        digest = package_slim(temporary_archive)
        observed = sha256_file(temporary_archive)
        if observed != digest:
            raise RuntimeError("packaged source digest changed before publication")
        packaged_source_identity = archive_source_identity(temporary_archive)
        if packaged_source_identity != source_identity:
            raise RuntimeError(
                "packaged source identity differs from the source tree admitted by the seal gate"
            )
        _require_packaged_architecture_fresh(temporary_archive)
        byte_count = _publish_archive_bytes(
            temporary_archive,
            ARCHIVE,
            expected_digest=digest,
            expected_source_identity=source_identity,
        )
    publish_text_file(SHA_FILE, f"{digest}  source.zip\n")
    publish_text_file(SHA_OUTPUT, f"{digest}\n")
    manifest = {
        "artifact": "source.zip",
        "byte_count": byte_count,
        "dependency_transport": dependency_transport,
        "seal_gate": {
            "canonical_goon_static_admission": "PASS",
            "generated_contracts": "PASS",
            "packaged_architecture": "PASS",
            "source_architecture": "PASS",
        },
        "sha256": digest,
        "source_identity": source_identity,
        "version": version,
    }
    publish_text_file(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Packaged retained source candidate {version}: sha256={digest} bytes={byte_count}")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Produce the canonical retained source package")
    parser.parse_args(argv)
    package_checkpoint()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
