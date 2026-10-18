from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

from oh_goon.repository_tooling.release.package_slim import archive_source_identity, package_slim

_NORMALIZED_MTIME = 1_580_601_600  # 2020-02-02T00:00:00Z


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
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


def _record_evidence(path: Path, base: dict[str, object]) -> Path:
    payload = dict(base)
    payload["evidence_identity"] = _sha256_json(base)
    return _atomic_write(
        path,
        (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )


def _load_evidence(path: Path, expected: dict[str, object]) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    evidence_identity = payload.get("evidence_identity")
    base = dict(payload)
    base.pop("evidence_identity", None)
    if evidence_identity != _sha256_json(base):
        return None
    return payload


def _zip_members(archive_path: Path) -> tuple[str, list[zipfile.ZipInfo]]:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
    file_infos = [info for info in infos if not info.is_dir()]
    if not file_infos:
        raise ValueError("source archive contains no files")
    prefixes: set[str] = set()
    for info in infos:
        pure = PurePosixPath(info.filename)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ValueError(f"unsafe source archive member: {info.filename}")
        prefixes.add(pure.parts[0])
        mode = info.external_attr >> 16
        if not info.is_dir() and mode and not stat.S_ISREG(mode):
            raise ValueError(f"source archive contains non-regular member: {info.filename}")
    if len(prefixes) != 1:
        raise ValueError("source archive must contain exactly one top-level directory")
    return next(iter(prefixes)), infos


def _archive_version(archive_path: Path) -> str:
    prefix, _infos = _zip_members(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        try:
            return archive.read(f"{prefix}/VERSION").decode("utf-8").strip()
        except (KeyError, UnicodeDecodeError) as exc:
            raise ValueError("source archive has no valid VERSION") from exc


def _extract_source_zip(archive_path: Path, destination: Path) -> None:
    prefix, infos = _zip_members(archive_path)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info in infos:
            pure = PurePosixPath(info.filename)
            if pure.parts[0] != prefix or len(pure.parts) == 1:
                continue
            relative = Path(*pure.parts[1:])
            target = destination / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, archive.read(info))


def _normalize_tree_times(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.utime(path, (_NORMALIZED_MTIME, _NORMALIZED_MTIME), follow_symlinks=False)
    os.utime(root, (_NORMALIZED_MTIME, _NORMALIZED_MTIME), follow_symlinks=False)


def verify_cumulative_patch_replay_impl(
    state_dir: Path,
    source_zip: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
    *,
    patch_path: Path,
    parent_input: Path,
    artifact_dir: Path,
    package_slim_fn=package_slim,
) -> Path:
    if patch_evidence.get("artifact_sha256") != _sha256_file(patch_path):
        raise RuntimeError("cumulative patch changed before replay")
    with tempfile.TemporaryDirectory(prefix="oh-goon-patch-replay-", dir=artifact_dir) as directory:
        workspace = Path(directory)
        replay_root = workspace / "source"
        _extract_source_zip(parent_input, replay_root)
        try:
            result = subprocess.run(
                ["git", "apply", "-p2", "--binary", "--whitespace=nowarn", str(patch_path)],
                cwd=replay_root,
                check=False,
                capture_output=True,
                env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("cumulative patch replay exceeded its 120s execution budget") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"cumulative patch replay failed with exit {result.returncode}: "
                + result.stderr.decode("utf-8", errors="replace")[:400]
            )
        replay_zip = workspace / source_zip.name
        replay_sha = package_slim_fn(replay_zip, root=replay_root)
        if replay_sha != source_zip_sha256 or _sha256_file(source_zip) != source_zip_sha256:
            raise RuntimeError(
                f"patch replay bytes differ from canonical source ZIP: replay={replay_sha} expected={source_zip_sha256}"
            )
    base: dict[str, object] = {
        "schema": 1,
        "status": "BYTE_IDENTICAL",
        "source_zip_sha256": source_zip_sha256,
        "patch_evidence_identity": patch_evidence["evidence_identity"],
        "replay_sha256": source_zip_sha256,
    }
    return _record_evidence(state_dir / "package-patch-replay.json", base)


def load_patch_replay_evidence(
    state_dir: Path,
    source_zip_sha256: str,
    patch_evidence: dict[str, object],
) -> dict[str, object] | None:
    return _load_evidence(
        state_dir / "package-patch-replay.json",
        {
            "status": "BYTE_IDENTICAL",
            "source_zip_sha256": source_zip_sha256,
            "patch_evidence_identity": patch_evidence["evidence_identity"],
            "replay_sha256": source_zip_sha256,
        },
    )


def _verify_packaged_dependency_scope(archive: zipfile.ZipFile, prefix: str) -> dict[str, object]:
    pyproject_name = f"{prefix}/backend/pyproject.toml"
    lock_name = f"{prefix}/backend/uv.lock"
    try:
        pyproject_bytes = archive.read(pyproject_name)
        lock_bytes = archive.read(lock_name)
        tomllib.loads(pyproject_bytes.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("fresh source ZIP dependency authorities are missing or unreadable") from exc
    return {
        "intent_sha256": hashlib.sha256(pyproject_bytes).hexdigest(),
        "backend_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "consumer_mode": "uv --frozen",
    }


def verify_fresh_source_zip(
    root: Path,
    state_dir: Path,
    source_zip: Path,
    source_zip_sha256: str,
    source_identity: str,
    seal_identity: str,
) -> Path:
    if _sha256_file(source_zip) != source_zip_sha256:
        raise RuntimeError("source ZIP changed before fresh-package acceptance")
    sidecar = source_zip.with_name(source_zip.name + ".sha256")
    expected_sidecar = f"{source_zip_sha256}  {source_zip.name}\n"
    if sidecar.read_text(encoding="utf-8") != expected_sidecar:
        raise RuntimeError("source ZIP checksum sidecar does not match exact artifact bytes")
    prefix, infos = _zip_members(source_zip)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if prefix != f"oh-goon-{version}" or _archive_version(source_zip) != version:
        raise RuntimeError("source ZIP version/prefix does not match current checkpoint")
    with zipfile.ZipFile(source_zip) as archive:
        corrupt = archive.testzip()
        dependency_scope = _verify_packaged_dependency_scope(archive, prefix)
    if corrupt is not None:
        raise RuntimeError(f"source ZIP integrity failed at member: {corrupt}")
    names = {PurePosixPath(info.filename).as_posix() for info in infos if not info.is_dir()}
    if f"{prefix}/backend/uv.lock" not in names:
        raise RuntimeError("committed backend/uv.lock is missing from public source ZIP")
    observed_identity = archive_source_identity(source_zip)
    if observed_identity != source_identity:
        raise RuntimeError("fresh source ZIP payload identity differs from sealed source")
    base: dict[str, object] = {
        "schema": 1,
        "status": "PASS",
        "source_zip_sha256": source_zip_sha256,
        "source_identity": source_identity,
        "seal_identity": seal_identity,
        "member_count": len(names),
        "archive_source_identity": observed_identity,
        "dependency_scope": dependency_scope,
    }
    return _record_evidence(state_dir / "package-fresh-zip-acceptance.json", base)


def load_fresh_zip_evidence(
    state_dir: Path,
    source_zip_sha256: str,
    source_identity: str,
    seal_identity: str,
) -> dict[str, object] | None:
    return _load_evidence(
        state_dir / "package-fresh-zip-acceptance.json",
        {
            "status": "PASS",
            "source_zip_sha256": source_zip_sha256,
            "source_identity": source_identity,
            "seal_identity": seal_identity,
            "archive_source_identity": source_identity,
        },
    )


