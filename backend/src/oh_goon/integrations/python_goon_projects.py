"""Capture and materialize complete Python Goons through existing source owners."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_resources import PythonGoonProject
from oh_goon.core.path_authority import repository_relative_parts
from oh_goon.core.path_layouts import RunPathLayout
from oh_goon.integrations.content_snapshot import replace_tree_from_tar_gz
from oh_goon.integrations.source_snapshot import snapshot_source_directory

MAX_PROJECT_ARCHIVE_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CapturedPythonGoonProject:
    identity: PythonGoonProject
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


def capture_python_goon_project(
    root: Path,
    compiled: CompiledExecutionTemplate,
    *,
    yaml_sha256: str,
    max_source_bytes: int = 64 * 1024 * 1024,
    max_archive_bytes: int = MAX_PROJECT_ARCHIVE_BYTES,
) -> CapturedPythonGoonProject:
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
        )
        if identity.byte_count > 64 * 1024 * 1024 or identity.entry_count > 4096:
            raise FleetPlanInvalid("Python Goon project exceeds source bounds")
        for name in ("goon.yml", "README.md", "pyproject.toml", "uv.lock"):
            path = snapshot / name
            if path.is_symlink() or not path.is_file():
                raise FleetPlanInvalid(f"Python Goon project requires a regular {name}")
        if sha256_file(snapshot / "goon.yml") != yaml_sha256:
            raise FleetPlanInvalid("Python Goon changed during source capture")
        for name in ("pyproject.toml", "uv.lock"):
            try:
                metadata = tomllib.loads((snapshot / name).read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise FleetPlanInvalid(f"Invalid Python Goon {name}") from exc
            _require_contained_dependency_paths(metadata)
            workspace: object = metadata
            for component in ("tool", "uv", "workspace"):
                workspace = workspace.get(component) if isinstance(workspace, dict) else None
            if isinstance(workspace, dict):
                for field in ("members", "exclude"):
                    _require_contained_dependency_paths({"path": workspace.get(field, [])})
        archive_path = Path(directory) / "project.tar.gz"
        with archive_path.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for child in sorted(snapshot.iterdir()):
                        archive.add(child, arcname=child.name, filter=_archive_metadata)
        if archive_path.stat().st_size > max_archive_bytes:
            raise FleetPlanInvalid("Python Goon archive exceeds source bounds")
        packet = archive_path.read_bytes()
        return CapturedPythonGoonProject(
            PythonGoonProject(
                content_sha256=identity.sha256,
                archive_sha256=hashlib.sha256(packet).hexdigest(),
                byte_count=identity.byte_count,
                entry_count=identity.entry_count,
            ),
            packet,
        )


def _archive_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.mtime = 0
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.pax_headers = {}
    return member


def _require_contained_dependency_paths(value: object) -> None:
    """Constrain native path authority, without resolving Python dependencies."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"path", "directory", "editable", "virtual", "backend-path"}:
                paths = item if isinstance(item, list) else [item]
                for path in paths:
                    if isinstance(path, str):
                        try:
                            repository_relative_parts(
                                str(PurePosixPath(path.replace("\\", "/"))),
                                label="Python Goon dependency path",
                            )
                        except FleetPlanInvalid:
                            raise FleetPlanInvalid(
                                "Python Goon dependency path escapes its project"
                            ) from None
            _require_contained_dependency_paths(item)
    elif isinstance(value, list):
        for item in value:
            _require_contained_dependency_paths(item)


def materialize_python_goon_project(
    packet: bytes, expected: PythonGoonProject, destination: Path
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
