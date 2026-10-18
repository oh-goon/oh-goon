from __future__ import annotations

import hashlib
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound, FleetPlanSourceStale
from oh_goon.core.models.fleet_plan_source_document import FleetPlanSourceDocument
from oh_goon.core.path_authority import ContainedPathSpace, navigation_path
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
    require_expected_exact_file_revision,
)


def describe_plan_source(root: Path, path: str, content: str) -> FleetPlanSourceDocument:
    """Describe candidate YAML bytes without reading or mutating the source file."""
    root, target = resolve_plan_source_path(root, path)
    return _document(root, target, content)


def read_plan_source(root: Path, path: str) -> FleetPlanSourceDocument:
    root, target = resolve_plan_source_path(root, path)
    if not target.is_file():
        raise FleetPlanSourceNotFound(f"Fleet plan source does not exist: {path}")
    try:
        with target.open("r", encoding="utf-8", newline="") as stream:
            content = stream.read()
    except (OSError, UnicodeError) as exc:
        raise FleetPlanInvalid(f"Cannot read Fleet plan source {target}: {exc}") from exc
    return _document(root, target, content)


def commit_plan_source(
    root: Path,
    path: str,
    *,
    expected_revision: str | None,
    content: str,
) -> FleetPlanSourceDocument:
    root, target = resolve_plan_source_path(root, path)
    with exact_file_write_lock(target):
        _require_expected_revision(target, expected_revision)
        if not target.parent.is_dir():
            raise FleetPlanInvalid(f"Fleet plan parent directory does not exist: {target.parent}")
        return _atomic_replace(root, target, expected_revision=expected_revision, content=content)


def resolve_plan_source_path(root: Path, path: str) -> tuple[Path, Path]:
    root = navigation_path(root)
    if not root.is_dir():
        raise FleetPlanInvalid(f"Fleet plan root is not a directory: {root}")
    if Path(path).suffix.lower() not in {".yml", ".yaml"}:
        raise FleetPlanInvalid("Fleet plan source path must be a relative .yml or .yaml file")
    target = ContainedPathSpace.from_root(root).resolve_from(
        root, path, label="Fleet plan source path"
    )
    return root, target


def _atomic_replace(
    root: Path,
    target: Path,
    *,
    expected_revision: str | None,
    content: str,
) -> FleetPlanSourceDocument:
    try:
        with prepared_text_file(target, content, newline="") as temporary:
            _require_expected_revision(target, expected_revision)
            replace_prepared_file(temporary, target)
    except OSError as exc:
        raise FleetPlanInvalid(f"Cannot write Fleet plan source {target}: {exc}") from exc
    return _document(root, target, content)


def _require_expected_revision(target: Path, expected_revision: str | None) -> None:
    require_expected_exact_file_revision(
        target,
        expected_revision,
        invalid_error=lambda: FleetPlanInvalid(f"Fleet plan source is not a file: {target}"),
        changed_error=lambda _current, _expected: FleetPlanSourceStale(
            "Fleet plan source changed before save; reload the current YAML revision"
        ),
        removed_error=lambda: FleetPlanSourceStale(
            "Fleet plan source was removed before save; reload the plan workspace"
        ),
    )


def _document(root: Path, target: Path, content: str) -> FleetPlanSourceDocument:
    return FleetPlanSourceDocument(
        root=str(root),
        path=str(
            ContainedPathSpace.from_root(root).relative(target, label="Fleet plan source document")
        ),
        revision=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content=content,
    )
