from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from oh_goon.core.file_digest import sha256_file, update_digest_from_file
from oh_goon.core.source_transport_metadata import is_disposable_source_transport_artifact_name
from oh_goon.repository_tooling.public_dependency_privacy import assert_public_source_privacy
from oh_goon.repository_tooling.release.source_version_coherence import (
    require_source_version_coherence,
)
from oh_goon.repository_tooling.source_layout import (
    is_transient_source_path,
    source_transport_artifact_issues,
)

ROOT = Path(__file__).resolve().parents[5]
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".oh-goon-playwright-browsers",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    "playwright-report",
    "playwright-runtime-report",
    "test-results",
}
EXCLUDED_FILES = {"coverage-python.json"}
OWNED_TOP_LEVEL = {
    ".dockerignore",
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    "AGENTS.md",
    "CHANGELOG.md",
    "CHECKPOINT_NOT_RELEASE.md",
    "HISTORY.md",
    "LICENSE",
    "Makefile",
    "README.md",
    "VERSION",
    "backend",
    "docker",
    "docker-compose.yml",
    "docs",
    "examples",
    "frontend",
    "generated",
    "quality-floors.json",
    "scripts",
    "templates",
    "template-certification",
    "tests",
    "vendor",
}


def _include(path: Path, root: Path = ROOT) -> bool:
    rel = path.relative_to(root)
    if is_transient_source_path(rel):
        return False
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    if is_disposable_source_transport_artifact_name(rel.name):
        return False
    if rel.name in EXCLUDED_FILES or rel.suffix in {".pyc", ".pyo", ".tsbuildinfo"}:
        return False
    if rel.parts[:3] in (
        ("generated", "release", "dist"),
        ("generated", "release", "static"),
    ):
        return False
    if rel.parts[:2] == ("frontend", "dist"):
        return False
    if rel.parts and rel.parts[0] in {".oh-goon-data", "repositories"}:
        return False
    return True


def _validate_top_level_ownership(root: Path = ROOT) -> None:
    allowed = OWNED_TOP_LEVEL | EXCLUDED_DIRS | EXCLUDED_FILES | {".oh-goon-data", "repositories"}
    unknown = sorted(
        path.name
        for path in root.iterdir()
        if path.name not in allowed and not is_transient_source_path(path.name)
    )
    if unknown:
        raise ValueError(
            f"slim source archive refuses unowned top-level entries: {', '.join(unknown[:5])}"
        )


def _assert_no_source_transport_artifacts(root: Path) -> None:
    issues = source_transport_artifact_issues(root)
    if not issues:
        return
    count = len(issues)
    raise ValueError(
        f"slim source archive refuses {count} source transport "
        f"artifact{'s' if count != 1 else ''}; "
        "run oh-goon-source-layout check --debug for exact paths"
    )


def _collect_source_files(root: Path = ROOT) -> list[Path]:
    root = root.resolve()
    _assert_no_source_transport_artifacts(root)
    files: list[Path] = []
    symlinks: list[Path] = []
    special: list[Path] = []

    # Prune transient/dependency directories *before* descending into them.  The old
    # ``root.rglob("*")`` implementation traversed backend/.venv and frontend/node_modules
    # even though every descendant was later discarded by ``_include``.  On initialized
    # WSL/DrvFS workspaces that turned a source-identity query into minutes of irrelevant
    # filesystem walking and made repository-state unit tests appear pathologically slow.
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        retained_dirs: list[str] = []
        for name in sorted(dirnames):
            path = current / name
            if path.is_symlink():
                if _include(path, root):
                    symlinks.append(path)
                continue
            if _include(path, root):
                retained_dirs.append(name)
        dirnames[:] = retained_dirs

        for name in sorted(filenames):
            path = current / name
            if not _include(path, root):
                continue
            if path.is_symlink():
                symlinks.append(path)
                continue
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                files.append(path)
            elif not stat.S_ISDIR(mode):
                special.append(path)

    if symlinks:
        rendered = ", ".join(path.relative_to(root).as_posix() for path in symlinks[:5])
        raise ValueError(f"slim source archive refuses symlinks: {rendered}")
    if special:
        rendered = ", ".join(path.relative_to(root).as_posix() for path in special[:5])
        raise ValueError(f"slim source archive refuses non-regular filesystem entries: {rendered}")
    folded: dict[str, list[Path]] = {}
    for path in files:
        folded.setdefault(path.relative_to(root).as_posix().casefold(), []).append(path)
    collisions = [paths for paths in folded.values() if len(paths) > 1]
    if collisions:
        rendered = ", ".join(
            "/".join(path.relative_to(root).as_posix() for path in paths)
            for paths in collisions[:5]
        )
        raise ValueError(
            f"slim source archive refuses case-insensitive path collisions: {rendered}"
        )
    _validate_top_level_ownership(root)
    return files


def canonical_source_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Return the exact packageable public-source file set in canonical order."""

    root = root.resolve()
    return tuple(
        sorted(_collect_source_files(root), key=lambda path: path.relative_to(root).as_posix())
    )


def _canonical_source_state(root: Path) -> tuple[tuple[str, int, int, int], ...]:
    """Return a cheap invalidation signature for the exact packageable source set.

    The canonical digest still binds file bytes.  This state tuple only avoids rereading
    every source file when callers request the same identity repeatedly in one process.
    ctime is included with mtime/size so same-size rewrites remain invalidating even when
    a tool preserves modification timestamps.
    """

    root = root.resolve()
    state: list[tuple[str, int, int, int]] = []
    for path in canonical_source_files(root):
        stat_result = path.stat()
        state.append(
            (
                path.relative_to(root).as_posix(),
                stat_result.st_size,
                stat_result.st_mtime_ns,
                stat_result.st_ctime_ns,
            )
        )
    return tuple(state)


@lru_cache(maxsize=8)
def _canonical_source_identity_cached(
    root_text: str, state: tuple[tuple[str, int, int, int], ...]
) -> str:
    root = Path(root_text)
    hasher = hashlib.sha256()
    for relative, _size, _mtime_ns, _ctime_ns in state:
        path = root / relative
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        update_digest_from_file(hasher, path)
        hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest()


def canonical_source_identity(root: Path = ROOT) -> str:
    """Hash the exact packageable public-source file set used by package_slim.

    Repeated calls for an unchanged tree reuse the byte digest process-locally.  Source
    mutation changes the state signature and therefore recomputes the exact byte hash.
    """

    root = root.resolve()
    state = _canonical_source_state(root)
    return _canonical_source_identity_cached(str(root), state)


def _archive_composition(output: Path) -> tuple[int, tuple[tuple[str, int], ...]]:
    by_owner: dict[str, int] = {}
    total = 0
    with zipfile.ZipFile(output) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = Path(info.filename).parts
            if len(parts) < 2:
                continue
            owner = parts[1]
            total += info.file_size
            by_owner[owner] = by_owner.get(owner, 0) + info.file_size
    ranked = tuple(sorted(by_owner.items(), key=lambda item: (-item[1], item[0])))
    return total, ranked


def _checksum_path(output: Path) -> Path:
    return output.with_name(output.name + ".sha256")


def _write_checksum(output: Path, digest: str) -> Path:
    checksum = _checksum_path(output)
    if checksum.is_symlink():
        raise ValueError("slim archive checksum output must not be a symlink")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{checksum.name}.",
            suffix=".tmp",
            dir=checksum.parent,
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as handle:
            temporary = Path(handle.name)
            handle.write(f"{digest}  {output.name}\n")
        temporary.chmod(0o644)
        temporary.replace(checksum)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return checksum


def _canonical_archive_rows(
    archive: zipfile.ZipFile,
) -> tuple[str, list[tuple[str, zipfile.ZipInfo]]]:
    files = [info for info in archive.infolist() if not info.is_dir()]
    prefixes = {Path(info.filename).parts[0] for info in files if Path(info.filename).parts}
    if len(prefixes) != 1:
        raise ValueError("source archive must contain exactly one top-level directory")
    prefix = next(iter(prefixes))
    if re.fullmatch(r"oh-goon-\d+\.\d+\.\d+", prefix) is None:
        raise ValueError(
            "source archive top-level directory must carry canonical Oh-Goon version identity"
        )
    rows = [_canonical_archive_row(info, prefix) for info in files]
    rows.sort(key=lambda item: item[0])
    if len({relative.casefold() for relative, _info in rows}) != len(rows):
        raise ValueError("source archive contains a case-insensitive path collision")
    return prefix, rows


def _canonical_archive_row(info: zipfile.ZipInfo, prefix: str) -> tuple[str, zipfile.ZipInfo]:
    member_type = stat.S_IFMT(info.external_attr >> 16)
    if member_type not in {0, stat.S_IFREG}:
        raise ValueError("source archive contains a non-regular file member")
    if "\\" in info.filename:
        raise ValueError("source archive member names must use canonical POSIX separators")
    raw_parts = info.filename.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("source archive member path is not canonical")
    parts = Path(info.filename).parts
    if len(parts) < 2 or parts[0] != prefix:
        raise ValueError(f"invalid source archive member: {info.filename}")
    relative = Path(*parts[1:]).as_posix()
    if is_disposable_source_transport_artifact_name(Path(relative).name):
        raise ValueError(
            "source archive contains forbidden source transport artifact; exact member paths are debug-only"
        )
    if is_transient_source_path(relative):
        raise ValueError(
            "source archive contains forbidden transient or local-only payload; exact member paths are debug-only"
        )
    return relative, info


def _require_archive_version_identity(
    archive: zipfile.ZipFile, prefix: str, rows: list[tuple[str, zipfile.ZipInfo]]
) -> None:
    version_rows = [info for relative, info in rows if relative == "VERSION"]
    if len(version_rows) != 1:
        raise ValueError("source archive must contain exactly one canonical VERSION member")
    try:
        payload_version = archive.read(version_rows[0]).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("source archive VERSION must be UTF-8 text") from exc
    if payload_version != prefix.removeprefix("oh-goon-"):
        raise ValueError("source archive VERSION does not match top-level version identity")


def archive_source_identity(archive_path: Path) -> str:
    """Hash the regular file payload of one canonical Oh-Goon source ZIP."""

    hasher = hashlib.sha256()
    with zipfile.ZipFile(archive_path) as archive:
        prefix, rows = _canonical_archive_rows(archive)
        _require_archive_version_identity(archive, prefix, rows)
        for relative, info in rows:
            hasher.update(relative.encode("utf-8"))
            hasher.update(b"\0")
            _update_digest_from_archive_member(hasher, archive, info)
            hasher.update(b"\0")
    return "sha256:" + hasher.hexdigest()


def _update_digest_from_archive_member(
    hasher: object, archive: zipfile.ZipFile, info: zipfile.ZipInfo
) -> None:
    with archive.open(info, "r") as member:
        while chunk := member.read(1024 * 1024):
            hasher.update(chunk)


def _generated_contract_projection_issues(root: Path) -> tuple[str, ...]:
    """Validate the committed frontend projection against the captured OpenAPI.

    Packaging must never transport a half-regenerated contract set.  This check is
    intentionally capture-local: it does not walk the live backend API, so it also
    works for deterministic replay roots that have no installed project environment.
    ``check-generated`` remains the stronger source-vs-capture authority check.
    """

    openapi_path = root / "generated" / "openapi" / "openapi.json"
    frontend_root = root / "generated" / "frontend"
    openapi_present = openapi_path.is_file()
    frontend_present = frontend_root.is_dir()
    if not openapi_present and not frontend_present:
        # Small unit-test fixture roots need not model generated contracts.
        return ()
    if not openapi_present:
        return ("generated/openapi/openapi.json is missing",)
    if not frontend_present:
        return ("generated/frontend is missing",)
    try:
        captured = json.loads(openapi_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (f"generated/openapi/openapi.json is unreadable: {exc}",)
    if not isinstance(captured, dict):
        return ("generated/openapi/openapi.json must contain an object",)

    from oh_goon.repository_tooling.generation.generate_api_types import render_generated_files

    expected = render_generated_files(captured)
    actual = {
        path.relative_to(frontend_root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(frontend_root.rglob("*"))
        if path.is_file()
    }
    issues = []
    for relative in sorted(set(expected) | set(actual)):
        if actual.get(relative) != expected.get(relative):
            issues.append(f"generated/frontend/{relative} is stale or unexpected")
    return tuple(issues)


def _require_generated_contract_projection(root: Path) -> None:
    issues = _generated_contract_projection_issues(root)
    if issues:
        raise ValueError(
            "slim source archive refuses stale generated contract projection: " + "; ".join(issues)
        )


def _write_slim_archive(output: Path, *, root: Path, prefix: str, files: list[Path]) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(prefix + rel)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.date_time = (2020, 2, 2, 0, 0, 0)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with path.open("rb") as source, archive.open(info, "w") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)


def package_slim(output: Path, *, root: Path | None = None) -> str:
    root = (ROOT if root is None else root).resolve()
    output = output.expanduser().absolute()
    if output.is_symlink():
        raise ValueError("slim archive output must not be a symlink")
    output = output.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("slim archive output must be outside the source tree")
    version_file = root / "VERSION"
    if version_file.is_symlink() or not version_file.is_file():
        raise ValueError("VERSION must be a regular non-symlink file")
    version = version_file.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError(f"VERSION must contain canonical x.y.z release identity, got {version!r}")
    assert_public_source_privacy(root)
    _require_generated_contract_projection(root)
    require_source_version_coherence(root, version)
    prefix = f"oh-goon-{version}/"
    files = list(canonical_source_files(root))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        _write_slim_archive(temporary, root=root, prefix=prefix, files=files)
        temporary.chmod(0o644)
        temporary.replace(output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    digest = sha256_file(output)
    _write_checksum(output, digest)
    return digest
