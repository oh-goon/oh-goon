from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.source_layout_policy import assert_canonical_source_layout
from oh_goon.repository_tooling._dependency_lock_portability import backend_lock_portability_issues
from oh_goon.repository_tooling.public_dependency_privacy import assert_public_source_privacy

ROOT = Path(__file__).resolve().parents[5]
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
BACKEND_LOCK = ROOT / "backend" / "uv.lock"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"
FRONTEND_LOCK = ROOT / "frontend" / "package-lock.json"
OPENAPI = ROOT / "generated" / "openapi" / "openapi.json"
GENERATED_FRONTEND = ROOT / "generated" / "frontend"
GOON_TOOL_AUTHORITY = ROOT / "backend" / "src" / "oh_goon" / "canonical_goon_tool_authority.json"


@dataclass(frozen=True, slots=True)
class ReleaseDependencyReceipt:
    """Exact incoming lock bytes transported through one release transition."""

    backend_lock_sha256: str
    frontend_lock_sha256: str


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _require_regular(path: Path, label: str) -> None:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must remain inside the checkout") from exc
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a contained regular non-symlink file")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _run(command: tuple[str, ...], *, cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(f"{command[0]} command failed: {detail}")
    return result.stdout.strip()


def canonical_version() -> str:
    _require_regular(VERSION_FILE, "VERSION")
    value = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"VERSION must contain a semantic x.y.z release, got {value!r}")
    return value


def _backend_version() -> str:
    _require_regular(PYPROJECT, "backend/pyproject.toml")
    return _run(("uv", "version", "--short"), cwd=ROOT / "backend")


def _frontend_version() -> str:
    _require_regular(FRONTEND_PACKAGE, "frontend/package.json")
    payload = _run(("npm", "pkg", "get", "version"), cwd=ROOT / "frontend")
    value = json.loads(payload)
    if not isinstance(value, str):
        raise RuntimeError("npm pkg get version did not return a string")
    return value


def _openapi_version() -> str | None:
    _require_regular(OPENAPI, "generated/openapi/openapi.json")
    payload = json.loads(OPENAPI.read_text(encoding="utf-8"))
    info = payload.get("info") if isinstance(payload, dict) else None
    value = info.get("version") if isinstance(info, dict) else None
    return value if isinstance(value, str) else None


def _assert_release_authoring_prerequisites() -> ReleaseDependencyReceipt:
    """Fail before effects and capture the incoming lock-byte receipt."""

    assert_canonical_source_layout(ROOT)
    certification_paths = _certification_source_version_files()
    for path, label in (
        (VERSION_FILE, "VERSION"),
        (PYPROJECT, "backend/pyproject.toml"),
        (BACKEND_LOCK, "backend/uv.lock"),
        (FRONTEND_PACKAGE, "frontend/package.json"),
        (FRONTEND_LOCK, "frontend/package-lock.json"),
        *((path, str(path.relative_to(ROOT))) for path in certification_paths),
    ):
        _require_regular(path, label)
    assert_public_source_privacy(ROOT)
    return ReleaseDependencyReceipt(
        backend_lock_sha256=_sha256_file(BACKEND_LOCK),
        frontend_lock_sha256=_sha256_file(FRONTEND_LOCK),
    )


def _post_authoring_dependency_receipt(
    incoming: ReleaseDependencyReceipt,
) -> ReleaseDependencyReceipt:
    """Prove only the native authority that actually changed during version authoring."""

    if _sha256_file(BACKEND_LOCK) != incoming.backend_lock_sha256:
        raise RuntimeError(
            "backend/uv.lock changed during release version authoring; dependency resolution authority must remain byte-identical"
        )
    assert_public_source_privacy(ROOT)
    return ReleaseDependencyReceipt(
        backend_lock_sha256=incoming.backend_lock_sha256,
        frontend_lock_sha256=_sha256_file(FRONTEND_LOCK),
    )


def _authoring_mutable_files() -> tuple[Path, ...]:
    files: list[Path] = [
        VERSION_FILE,
        PYPROJECT,
        BACKEND_LOCK,
        FRONTEND_PACKAGE,
        FRONTEND_LOCK,
        OPENAPI,
        GOON_TOOL_AUTHORITY,
        *_certification_source_version_files(),
    ]
    if GENERATED_FRONTEND.is_dir():
        files.extend(path for path in GENERATED_FRONTEND.rglob("*") if path.is_file())
    return tuple(dict.fromkeys(files))


def _capture_authoring_state() -> dict[Path, bytes | None]:
    snapshot: dict[Path, bytes | None] = {}
    for path in _authoring_mutable_files():
        if path.is_symlink():
            raise ValueError(
                f"release authoring refuses symlink mutable authority: {path.relative_to(ROOT)}"
            )
        snapshot[path] = path.read_bytes() if path.is_file() else None
    return snapshot


def _restore_authoring_state(snapshot: dict[Path, bytes | None]) -> None:
    known = set(snapshot)
    if GENERATED_FRONTEND.exists():
        for path in sorted(GENERATED_FRONTEND.rglob("*"), reverse=True):
            if path.is_file() and path not in known:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for path, payload in snapshot.items():
        if payload is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _regenerate_generated_authority() -> None:
    from oh_goon.repository_tooling.generation.check_generated import main as check_generated
    from oh_goon.repository_tooling.generation.export_openapi import (
        openapi_document,
        write_openapi,
    )
    from oh_goon.repository_tooling.generation.generate_api_types import (
        load_captured_openapi,
        write_generated_api_types,
    )
    from oh_goon.repository_tooling.generation.goon_tool_authority import (
        write_goon_tool_authority,
    )

    document = openapi_document()
    write_openapi(document, output=OPENAPI)
    # Frontend authority is derived from the serialized OpenAPI capture, exactly
    # like check-generated. Generating from the in-memory object first can produce
    # different deterministic shard boundaries once the sorted JSON is reloaded.
    captured_document = load_captured_openapi(OPENAPI)
    write_generated_api_types(captured_document, generated_dir=GENERATED_FRONTEND)
    write_goon_tool_authority(root=ROOT, output=GOON_TOOL_AUTHORITY)
    if check_generated() != 0:
        raise RuntimeError("generated authority did not converge after release version authoring")


def _source_identity_matches(version: str, receipt: ReleaseDependencyReceipt) -> bool:
    return (
        canonical_version() == version
        and _backend_version() == version
        and _frontend_version() == version
        and _sha256_file(BACKEND_LOCK) == receipt.backend_lock_sha256
        and _sha256_file(FRONTEND_LOCK) == receipt.frontend_lock_sha256
    )


def observed_versions() -> dict[str, str | None]:
    return {
        "VERSION": canonical_version(),
        "backend/pyproject": _backend_version(),
        "frontend": _frontend_version(),
        "openapi": _openapi_version(),
    }


def check() -> int:
    expected = canonical_version()
    versions = observed_versions()
    drift = {name: value for name, value in versions.items() if value != expected}
    if not drift:
        print(f"Release identity is consistent at {expected}")
        return 0
    if drift:
        rendered = ", ".join(f"{name}={value!r}" for name, value in drift.items())
        print(f"Release identity drift: expected {expected}; {rendered}")
    return 1


def _stamp_native(version: str) -> None:
    # Version stamping is release identity authoring, not dependency authoring.
    # The committed uv.lock is already exact resolved authority and must remain
    # byte-identical. Native uv owns the pyproject mutation through --frozen.
    portability = backend_lock_portability_issues(BACKEND_LOCK)
    if portability:
        rendered = "; ".join(portability)
        raise RuntimeError("backend/uv.lock is non-portable before version stamp: " + rendered)
    lock_before = BACKEND_LOCK.read_bytes()
    _run(("uv", "version", "--frozen", version), cwd=ROOT / "backend")
    if BACKEND_LOCK.read_bytes() != lock_before:
        raise RuntimeError(
            "uv version --frozen mutated backend/uv.lock; release version authoring must leave committed resolution authority byte-identical"
        )
    portability = backend_lock_portability_issues(BACKEND_LOCK)
    if portability:
        rendered = "; ".join(portability)
        raise RuntimeError("native uv authored a non-portable backend/uv.lock: " + rendered)
    _run(
        ("npm", "version", version, "--no-git-tag-version", "--allow-same-version"),
        cwd=ROOT / "frontend",
    )


def _certification_source_version_files() -> tuple[Path, ...]:
    certification_root = ROOT / "docs" / "certification"
    if not certification_root.is_dir():
        return ()
    files: list[Path] = []
    for path in sorted(certification_root.rglob("*.yml")):
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?m)^source_version:[ \t]*[^\s#]+[ \t]*$", text):
            files.append(path)
    return tuple(files)


def _stamp_certification_source_versions(version: str, paths: tuple[Path, ...]) -> None:
    for path in paths:
        _require_regular(path, str(path.relative_to(ROOT)))
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r"(?m)^source_version:[ \t]*[^\s#]+[ \t]*$",
            f"source_version: {version}",
            text,
        )
        if count != 1:
            raise ValueError(
                f"{path.relative_to(ROOT)} must contain exactly one source_version field"
            )
        _write_text(path, updated)


def stamp() -> None:
    version = canonical_version()
    certification_paths = _certification_source_version_files()
    owners = (
        (PYPROJECT, "backend/pyproject.toml"),
        (BACKEND_LOCK, "backend/uv.lock"),
        (FRONTEND_PACKAGE, "frontend/package.json"),
        (FRONTEND_LOCK, "frontend/package-lock.json"),
        *((path, str(path.relative_to(ROOT))) for path in certification_paths),
    )
    for path, label in owners:
        _require_regular(path, label)
    originals = {path: path.read_bytes() for path, _label in owners}
    try:
        _stamp_native(version)
        _stamp_certification_source_versions(version, certification_paths)
    except BaseException:
        for path, payload in originals.items():
            path.write_bytes(payload)
        raise
    print(
        f"Stamped source release identity to {version} with native uv/npm version commands; backend/uv.lock remained byte-identical; regenerate contracts next"
    )


def set_version(value: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"release version must be canonical x.y.z, got {value!r}")
    incoming_receipt = _assert_release_authoring_prerequisites()
    snapshot = _capture_authoring_state()
    try:
        if _source_identity_matches(value, incoming_receipt):
            print(
                f"Source release identity is already stamped to {value}; "
                "regenerating owned generated authority before completing the transition"
            )
            post_receipt = incoming_receipt
        else:
            _write_text(VERSION_FILE, value + "\n")
            stamp()
            post_receipt = _post_authoring_dependency_receipt(incoming_receipt)
        _regenerate_generated_authority()
        versions = observed_versions()
        drift = {name: observed for name, observed in versions.items() if observed != value}
        if drift:
            rendered = ", ".join(f"{name}={observed!r}" for name, observed in drift.items())
            raise RuntimeError(f"release identity did not converge after authoring: {rendered}")
        if _sha256_file(BACKEND_LOCK) != post_receipt.backend_lock_sha256:
            raise RuntimeError(
                "backend dependency receipt changed after generated-authority regeneration"
            )
        if _sha256_file(FRONTEND_LOCK) != post_receipt.frontend_lock_sha256:
            raise RuntimeError(
                "frontend dependency receipt changed after generated-authority regeneration"
            )
        assert_canonical_source_layout(ROOT)
    except BaseException:
        _restore_authoring_state(snapshot)
        raise
    print(
        f"Release authoring transition complete at {value}; source admission, native version authority, dependency evidence, and generated authority are converged"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Set or check oh-goon release identity")
    parser.add_argument("mode", choices=("set", "check"))
    parser.add_argument("version", nargs="?")
    args = parser.parse_args()
    if args.mode == "set":
        if args.version is None:
            parser.error("set requires a canonical x.y.z version")
        set_version(args.version)
        return 0
    if args.version is not None:
        parser.error(f"{args.mode} does not accept a version argument")
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
