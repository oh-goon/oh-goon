from __future__ import annotations

import configparser
import hashlib
import os
import re
import stat
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.source_tree_policy import is_transient_source_path

ROOT = Path(__file__).resolve().parents[5]


def _contained_in_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _expected_payload(
    root: Path, *, template_source: Path | None = None
) -> tuple[dict[str, tuple[str, int]], tuple[str, ...]]:
    if template_source is None:
        from oh_goon.repository_tooling.release.build_wheel import staged_template_catalogs

        with staged_template_catalogs(root) as stage:
            return _expected_payload(root, template_source=stage)
    expected: dict[str, tuple[str, int]] = {}
    issues: list[str] = []
    sources = (
        (root / "backend" / "src" / "oh_goon", "oh_goon"),
        (root / "generated" / "release" / "static", "oh_goon/static"),
        (template_source / "goons", "oh_goon/templates/goons"),
        (template_source / "plans", "oh_goon/templates/plans"),
    )
    for source, prefix in sources:
        owner_root = template_source if source.parent == template_source else root
        if source.is_symlink() or not source.is_dir() or not _contained_in_root(source, owner_root):
            issues.append(
                "managed release source must be a contained regular non-symlink directory: "
                f"{source.relative_to(owner_root)}"
            )
            continue
        for current, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for dirname in dirnames:
                if is_transient_source_path((current_path / dirname).relative_to(source)):
                    continue
                directory = current_path / dirname
                if directory.is_symlink():
                    issues.append(
                        f"managed release source contains symlink: {directory.relative_to(owner_root)}"
                    )
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs
            for filename in filenames:
                if is_transient_source_path((current_path / filename).relative_to(source)):
                    continue
                path = current_path / filename
                if path.is_symlink():
                    issues.append(
                        f"managed release source contains symlink: {path.relative_to(owner_root)}"
                    )
                    continue
                if not stat.S_ISREG(path.lstat().st_mode):
                    issues.append(
                        f"managed release source contains non-regular file: {path.relative_to(owner_root)}"
                    )
                    continue
                expected[f"{prefix}/{path.relative_to(source).as_posix()}"] = sha256_file_with_size(
                    path
                )
    return expected, tuple(issues)


def _project_scripts(root: Path) -> dict[str, str]:
    path = root / "backend" / "pyproject.toml"
    if path.is_symlink() or not path.is_file() or not _contained_in_root(path, root):
        raise ValueError("backend/pyproject.toml must be a contained regular non-symlink file")
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("backend/pyproject.toml is missing [project]")
    scripts = project.get("scripts", {})
    if not isinstance(scripts, dict) or not all(
        isinstance(name, str) and isinstance(target, str) for name, target in scripts.items()
    ):
        raise ValueError("backend/pyproject.toml project.scripts must be a string table")
    return dict(sorted(scripts.items()))


def _entry_point_issues(archive: zipfile.ZipFile, entry_path: str, root: Path) -> list[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(archive.read(entry_path).decode("utf-8"))
        expected = _project_scripts(root)
    except (
        OSError,
        UnicodeDecodeError,
        configparser.Error,
        tomllib.TOMLDecodeError,
        ValueError,
    ) as exc:
        return [f"release entry point cannot be verified: {exc}"]
    observed = (
        dict(sorted(parser.items("console_scripts")))
        if parser.has_section("console_scripts")
        else {}
    )
    return (
        []
        if observed == expected
        else ["release wheel console_scripts do not match backend/pyproject.toml"]
    )


def _archive_member_identity(archive: zipfile.ZipFile, name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    with archive.open(name, "r") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _archive_issues(
    archive: zipfile.ZipFile, root: Path, version: str, *, template_source: Path | None = None
) -> list[str]:
    issues: list[str] = []
    names = archive.namelist()
    if len(names) != len(set(names)):
        issues.append("release wheel contains duplicate entries")

    expected, source_issues = _expected_payload(root, template_source=template_source)
    issues.extend(source_issues)
    for name, identity in expected.items():
        if name not in names:
            issues.append(f"release wheel is missing managed payload: {name}")
        elif _archive_member_identity(archive, name) != identity:
            issues.append(f"release wheel managed payload differs from staged source: {name}")
    for name in names:
        if name.startswith("oh_goon/") and not name.endswith("/") and name not in expected:
            issues.append(f"release wheel contains stale managed payload: {name}")

    dist_info = f"oh_goon-{version}.dist-info"
    metadata_path = f"{dist_info}/METADATA"
    entry_path = f"{dist_info}/entry_points.txt"
    for required in (metadata_path, entry_path, "oh_goon/static/index.html"):
        if required not in names:
            issues.append(f"release wheel is missing required payload: {required}")

    if metadata_path in names:
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        if metadata.get("Name") != "oh-goon":
            issues.append("release wheel metadata Name must be 'oh-goon'")
        if metadata.get("Version") != version:
            issues.append(f"release wheel metadata Version must be {version!r}")
    if entry_path in names:
        issues.extend(_entry_point_issues(archive, entry_path, root))
    return issues


def release_artifact_issues(
    root: Path = ROOT, *, template_source: Path | None = None
) -> tuple[str, ...]:
    version_file = root / "VERSION"
    if (
        version_file.is_symlink()
        or not version_file.is_file()
        or not _contained_in_root(version_file, root)
    ):
        return ("VERSION must be a contained regular non-symlink file",)
    try:
        version = version_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        return (f"VERSION cannot be read: {exc}",)
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        return (f"VERSION must contain canonical x.y.z release identity, got {version!r}",)

    dist = root / "generated" / "release" / "dist"
    if dist.is_symlink() or not dist.is_dir() or not _contained_in_root(dist, root):
        return ("release dist must be a contained regular non-symlink directory",)
    entries = sorted(dist.iterdir())
    wheels = [path for path in entries if path.suffix == ".whl"]
    if len(wheels) != 1:
        return (f"release dist must contain exactly one wheel; found {len(wheels)}",)
    wheel = wheels[0]
    if any(path != wheel for path in entries):
        return ("release dist must contain only the release wheel",)
    if wheel.is_symlink() or not wheel.is_file() or not _contained_in_root(wheel, root):
        return ("release wheel must be a contained regular non-symlink file",)

    expected_name = f"oh_goon-{version}-py3-none-any.whl"
    issues: list[str] = []
    if wheel.name != expected_name:
        issues.append(f"release wheel filename is {wheel.name!r}, expected {expected_name!r}")
    try:
        with zipfile.ZipFile(wheel) as archive:
            issues.extend(_archive_issues(archive, root, version, template_source=template_source))
    except FleetDomainError as exc:
        issues.append(f"release source cannot be verified: {exc}")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        issues.append(f"release wheel cannot be inspected: {exc}")
    return tuple(issues)


def main() -> int:
    issues = release_artifact_issues(ROOT)
    if issues:
        print("Release wheel verification failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("Release wheel identity, entry point and managed Oh Goon payload are verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
