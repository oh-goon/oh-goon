"""Capture and materialize complete Python Goons through existing source owners."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_resources import CapturedProjectSource
from oh_goon.core.models.git_sources import LocalApplicationSource, LockedExecutionSource
from oh_goon.core.path_authority import ContainedPathSpace, repository_relative_parts
from oh_goon.core.path_layouts import RunPathLayout
from oh_goon.integrations.content_snapshot import replace_tree_from_tar_gz
from oh_goon.integrations.source_snapshot import snapshot_source_directory

MAX_PROJECT_ARCHIVE_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CapturedProject:
    identity: CapturedProjectSource
    archive: bytes


def python_goon_generated_paths(compiled: CompiledExecutionTemplate) -> tuple[str, ...]:
    """Project-local generated declarations consumed by source capture and packaging."""
    outputs: set[str] = {"goon.lock"}
    for step in compiled.steps:
        working_directory = PurePosixPath(
            getattr(step, "working_directory", ".").replace("\\", "/")
        )
        for declaration in (*getattr(step, "artifacts", ()), *getattr(step, "outputs", ())):
            value = getattr(declaration, "path", None)
            if isinstance(value, str) and "${" not in value:
                output_path = PurePosixPath(value.replace("\\", "/"))
                if output_path.is_absolute() or ".." in output_path.parts:
                    raise FleetPlanInvalid("Python Goon output path must be contained")
                outputs.add(str(working_directory / output_path))
    return tuple(sorted(outputs))


def capture_project_source(
    root: Path,
    compiled: CompiledExecutionTemplate,
    *,
    yaml_sha256: str | None,
    application: LocalApplicationSource | LockedExecutionSource | None = None,
    python_project: bool = True,
    uv_project: str = ".",
    max_source_bytes: int = 64 * 1024 * 1024,
    max_archive_bytes: int = MAX_PROJECT_ARCHIVE_BYTES,
) -> CapturedProject:
    """Freeze authored bytes once; Hashmarks retains package-content authority."""
    with tempfile.TemporaryDirectory(prefix="oh-goon-python-source-") as directory:
        snapshot = Path(directory) / "project"
        identity = snapshot_source_directory(
            root,
            snapshot,
            label="Python Goon project",
            exclude_paths=python_goon_generated_paths(compiled),
            max_bytes=max_source_bytes,
            max_entries=4096,
            reject_dotenv_state=application is not None,
        )
        if identity.byte_count > 64 * 1024 * 1024 or identity.entry_count > 4096:
            raise FleetPlanInvalid("Python Goon project exceeds source bounds")
        required: tuple[str, ...] = ("goon.yml", "README.md") if application is None else ()
        if python_project:
            if application is None:
                required += ("pyproject.toml", "uv.lock")
            else:
                required += (str(PurePosixPath(uv_project) / "pyproject.toml"),)
        for name in required:
            path = snapshot / name
            if path.is_symlink() or not path.is_file():
                raise FleetPlanInvalid(f"Python Goon project requires a regular {name}")
        if application is None and sha256_file(snapshot / "goon.yml") != yaml_sha256:
            raise FleetPlanInvalid("Python Goon changed during source capture")
        if python_project:
            selected_metadata = ContainedPathSpace.from_root(snapshot).resolve_from(
                snapshot,
                str(PurePosixPath(uv_project) / "pyproject.toml"),
                label="Captured uv project metadata",
                kind="file",
                require_exists=True,
            )
            _validate_project_metadata(snapshot, project_root=selected_metadata.parent)
        archive_path = Path(directory) / "project.tar.gz"
        with archive_path.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for child in sorted(snapshot.iterdir()):
                        archive.add(child, arcname=child.name, filter=_archive_metadata)
        if archive_path.stat().st_size > max_archive_bytes:
            raise FleetPlanInvalid("Python Goon archive exceeds source bounds")
        packet = archive_path.read_bytes()
        return CapturedProject(
            CapturedProjectSource(
                content_sha256=identity.sha256,
                archive_sha256=hashlib.sha256(packet).hexdigest(),
                byte_count=identity.byte_count,
                entry_count=identity.entry_count,
                application=application,
            ),
            packet,
        )


def _archive_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.mtime = 0
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.pax_headers = {}
    return member


def _validate_project_metadata(root: Path, *, project_root: Path) -> None:
    space = ContainedPathSpace.from_root(root)
    # Native uv may use metadata on the selected project's ancestor chain.
    # Inspect only that exact chain, not unrelated projects in the source tree.
    ancestors = [root]
    current = root
    for part in project_root.relative_to(root).parts:
        current /= part
        ancestors.append(current)
    if not any(
        (directory / "uv.lock").is_file() and not (directory / "uv.lock").is_symlink()
        for directory in ancestors
    ):
        raise FleetPlanInvalid(
            "Captured uv project requires a regular uv.lock on its captured ancestor chain"
        )
    pending = [
        directory / name
        for directory in ancestors
        for name in ("pyproject.toml", "uv.lock", "uv.toml")
        if (directory / name).is_file()
    ]
    seen: set[Path] = set(pending)
    for path in pending:
        try:
            metadata = tomllib.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise FleetPlanInvalid(f"Invalid Python Goon {path.relative_to(root)}") from exc
        base = path.parent
        _require_contained_dependency_paths(metadata, space=space, base=base)
        groups = metadata.get("dependency-groups")
        if isinstance(groups, dict):
            for requirements in groups.values():
                _require_contained_file_requirements(requirements, space=space, base=base)
        project = metadata.get("project")
        optional = project.get("optional-dependencies") if isinstance(project, dict) else None
        if isinstance(optional, dict):
            for requirements in optional.values():
                _require_contained_file_requirements(requirements, space=space, base=base)
        if path.name == "uv.lock":
            # The native lock identifies installed local projects. Inspect exactly
            # those projects' authored build metadata, without searching for other
            # pyprojects (which may intentionally be invalid test resources).
            packages = metadata.get("package", [])
            if isinstance(packages, list):
                for package in packages:
                    source = package.get("source") if isinstance(package, dict) else None
                    if not isinstance(source, dict):
                        continue
                    for field in ("directory", "editable"):
                        location = source.get(field)
                        if isinstance(location, str):
                            directory = space.resolve_from(
                                base, location, label="Python Goon local dependency"
                            )
                            for name in ("pyproject.toml", "uv.toml"):
                                child = directory / name
                                if child not in seen:
                                    seen.add(child)
                                    if child.is_file():
                                        pending.append(child)


def _require_contained_dependency_paths(
    value: object, *, space: ContainedPathSpace, base: Path
) -> None:
    """Constrain native path authority, without resolving Python dependencies."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "workspace" and isinstance(item, dict):
                for field in ("members", "exclude"):
                    _require_contained_dependency_paths(
                        {"path": item.get(field, [])}, space=space, base=base
                    )
            if key in {"path", "directory", "editable", "virtual", "backend-path", "workspace"}:
                paths = item if isinstance(item, list) else [item]
                for path in paths:
                    if isinstance(path, str):
                        _require_contained_dependency_path(path, space=space, base=base)
            elif key in {
                "url",
                "git",
                "registry",
                "find-links",
                "index-url",
                "extra-index-url",
                "default-index",
            }:
                locations = item if isinstance(item, list) else [item]
                for location in locations:
                    if isinstance(location, str):
                        _require_contained_dependency_location(location, space=space, base=base)
            elif key in {
                "requires",
                "dependencies",
                "constraint-dependencies",
                "override-dependencies",
                "build-constraint-dependencies",
                "extra-build-dependencies",
                "requires-dist",
            }:
                _require_contained_file_requirements(item, space=space, base=base)
            _require_contained_dependency_paths(item, space=space, base=base)
    elif isinstance(value, list):
        for item in value:
            _require_contained_dependency_paths(item, space=space, base=base)


def _require_contained_dependency_path(path: str, *, space: ContainedPathSpace, base: Path) -> None:
    try:
        normalized = str(PurePosixPath(path.replace("\\", "/")))
        if base == space.root:
            repository_relative_parts(normalized, label="Python Goon dependency path")
        else:
            space.resolve_from(base, normalized, label="Python Goon dependency path")
    except FleetPlanInvalid:
        raise FleetPlanInvalid("Python Goon dependency path escapes its project") from None


def _require_contained_dependency_location(
    location: str, *, space: ContainedPathSpace, base: Path
) -> None:
    """Distinguish remote URLs from native local index/artifact references only."""
    if PureWindowsPath(location).drive:
        _require_contained_dependency_path(location, space=space, base=base)
        return
    try:
        parsed = urlsplit(location)
    except ValueError:
        raise FleetPlanInvalid("Invalid Python Goon dependency location") from None
    if parsed.scheme.lower() == "file":
        # A file authority names a host/UNC share. Absolute file URLs also cannot
        # move with the captured project; only relative file references can.
        if parsed.netloc:
            raise FleetPlanInvalid("Python Goon dependency path escapes its project")
        _require_contained_dependency_path(unquote(parsed.path), space=space, base=base)
    elif not parsed.scheme:
        _require_contained_dependency_path(location, space=space, base=base)


def _require_contained_file_requirements(
    requirements: object, *, space: ContainedPathSpace, base: Path
) -> None:
    """Check direct file-URL custody; native uv still parses/resolves requirements."""
    if isinstance(requirements, dict):
        for requirement in requirements.values():
            _require_contained_file_requirements(requirement, space=space, base=base)
        return
    if isinstance(requirements, list):
        for requirement in requirements:
            _require_contained_file_requirements(requirement, space=space, base=base)
    elif isinstance(requirements, str):
        _, separator, reference = requirements.partition("@")
        location = (reference if separator else requirements).strip().split(maxsplit=1)
        if location and location[0].lower().startswith("file:"):
            _require_contained_dependency_location(location[0], space=space, base=base)


def materialize_python_goon_project(
    packet: bytes, expected: CapturedProjectSource, destination: Path
) -> None:
    if (
        len(packet) > MAX_PROJECT_ARCHIVE_BYTES
        or hashlib.sha256(packet).hexdigest() != expected.archive_sha256
    ):
        raise FleetPlanInvalid("Python Goon source delivery differs from resolved authority")
    with io.BytesIO(packet) as stream, tarfile.open(fileobj=stream, mode="r|gz") as archive:
        entry_count = 0
        byte_count = 0
        for member in archive:
            entry_count += 1
            byte_count += member.size
            if entry_count > 4096 or byte_count > 64 * 1024 * 1024:
                raise FleetPlanInvalid("Python Goon source delivery exceeds source bounds")
    observed = replace_tree_from_tar_gz(packet, destination, label="Python Goon project")
    if (
        observed.sha256 != expected.content_sha256
        or observed.byte_count != expected.byte_count
        or observed.entry_count != expected.entry_count
    ):
        raise FleetPlanInvalid("Python Goon materialized source identity mismatch")


def materialize_plan_projects(plan: ResolvedFleetPlan, layout: RunPathLayout) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for step in plan.steps:
        project = getattr(step, "project_source", None)
        if project is None or project.content_sha256 in sources:
            continue
        packet = plan._project_archives.get(project.content_sha256)
        if packet is None:
            raise FleetPlanInvalid("Python Goon captured source delivery is unavailable")
        destination = layout.package_source(project.content_sha256)
        materialize_python_goon_project(packet, project, destination)
        sources[project.content_sha256] = destination
    return sources
