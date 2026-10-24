"""Durable exact-identity receipts for deterministic pytest batch members.

This module is intentionally lightweight. It owns correctness identity, execution-plan
provenance, and atomic receipt state only; it does not discover tests, execute pytest, or
import test/product modules at module import time.  That separation keeps resume
bookkeeping cheap and prevents a durability helper from pulling large repository graphs
into every runner process.

Correctness reuse is deliberately task-level.  Batch grouping, worker count, and timeout
budgets are execution/performance facts and therefore do not invalidate an already-proven
deterministic task.  Resumed timing is still non-comparable with a clean run.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

RECEIPT_SCHEMA = "oh-goon.pytest-member-receipt/v2"
RESUME_SCHEMA = "oh-goon.pytest-resume-context/v2"
EXECUTION_PLAN_SCHEMA = "oh-goon.pytest-execution-plan/v1"
CONTEXT_MANIFEST_SCHEMA = "oh-goon.pytest-resume-context-manifest/v1"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_identity(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _file_identity(path: Path) -> str:
    from oh_goon.core.file_digest import sha256_file

    try:
        return "sha256:" + sha256_file(path)
    except OSError:
        return "missing"


def _member_file(member: str, *, root: Path) -> Path:
    path_text = member.partition("::")[0]
    path = Path(path_text)
    return path if path.is_absolute() else root / path


def test_plan_identity(members: Iterable[str], *, root: Path) -> str:
    """Bind the exact ordered pytest members and their test-file bytes."""

    file_identities: dict[str, str] = {}
    ordered: list[dict[str, str]] = []
    for member in members:
        path = _member_file(member, root=root)
        key = str(path.resolve())
        identity = file_identities.setdefault(key, _file_identity(path))
        ordered.append({"member": member, "test_file_sha256": identity})
    return _canonical_identity({"schema": "oh-goon.pytest-plan/v1", "members": ordered})


def python_toolchain_identity() -> str:
    """Bind Python/runtime package facts without installation-path identity."""

    distributions: list[tuple[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            distributions.append((name.casefold(), distribution.version))
    distributions.sort()
    payload = {
        "schema": "oh-goon.python-test-toolchain/v1",
        "implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "cache_tag": sys.implementation.cache_tag,
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_machine": platform.machine(),
        "distributions": distributions,
    }
    return _canonical_identity(payload)


def default_resume_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    state_home = Path(base) if base else Path.home() / ".local" / "state"
    return state_home / "oh-goon" / "pytest-member-receipts" / "schema-2"


def _safe_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    return normalized[:96] or "default"


@dataclass(frozen=True, slots=True)
class ResumeContext:
    source_identity: str
    test_plan_identity: str
    toolchain_identity: str
    suite_identity: str
    receipt_directory: Path
    resume_key: str
    granularity: str
    marker: str
    extra_context: tuple[str, ...]

    def manifest_payload(self) -> dict[str, object]:
        return {
            "schema": CONTEXT_MANIFEST_SCHEMA,
            "source_identity": self.source_identity,
            "test_plan_identity": self.test_plan_identity,
            "toolchain_identity": self.toolchain_identity,
            "suite_identity": self.suite_identity,
            "resume_key": self.resume_key,
            "granularity": self.granularity,
            "marker": self.marker,
            "extra_context": list(self.extra_context),
        }

    def ensure_manifest(self) -> Path:
        path = self.receipt_directory / "context.json"
        expected = self.manifest_payload()
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            current = None
        if current != expected:
            _atomic_json_write(path, expected)
        return path


def build_resume_context(
    *,
    root: Path,
    members: tuple[str, ...],
    resume_key: str,
    state_root: Path | None,
    granularity: str,
    marker: str | None,
    extra_context: tuple[str, ...] = (),
) -> ResumeContext:
    # Lazy import is deliberate: parsing/inspecting receipts must not import the source
    # packager or the repository-understanding graph.
    from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

    source_identity = canonical_source_identity(root)
    plan_identity = test_plan_identity(members, root=root)
    toolchain_identity = python_toolchain_identity()
    normalized_extra = tuple(extra_context)
    # Correctness identity deliberately excludes execution grouping/budgets. A completed
    # deterministic task remains correct if the same exact task is regrouped later.
    suite_identity = _canonical_identity(
        {
            "schema": RESUME_SCHEMA,
            "source_identity": source_identity,
            "test_plan_identity": plan_identity,
            "toolchain_identity": toolchain_identity,
            "resume_key": resume_key,
            "granularity": granularity,
            "marker": marker or "",
            "extra_context": list(normalized_extra),
        }
    )
    directory = (
        (state_root or default_resume_state_root())
        / _safe_key(resume_key)
        / suite_identity.removeprefix("sha256:")
    )
    return ResumeContext(
        source_identity=source_identity,
        test_plan_identity=plan_identity,
        toolchain_identity=toolchain_identity,
        suite_identity=suite_identity,
        receipt_directory=directory,
        resume_key=resume_key,
        granularity=granularity,
        marker=marker or "",
        extra_context=normalized_extra,
    )


def execution_plan_identity(
    *,
    suite_identity: str,
    batch_size: int,
    batch_timeout: int,
    timeout_bisection: bool,
    workers: int,
    suite_timeout: int | None,
    environment_retries: int,
    selected_batch: int | None,
    scheduled_members: tuple[str, ...],
    reused_members: tuple[str, ...],
) -> str:
    """Bind actual execution/performance provenance separately from correctness."""

    return _canonical_identity(
        {
            "schema": EXECUTION_PLAN_SCHEMA,
            "suite_identity": suite_identity,
            "batch_size": batch_size,
            "batch_timeout": batch_timeout,
            "timeout_bisection": timeout_bisection,
            "workers": workers,
            "suite_timeout": suite_timeout,
            "environment_retries": environment_retries,
            "selected_batch": selected_batch,
            "scheduled_members": list(scheduled_members),
            "reused_members": list(reused_members),
        }
    )


def batch_identity(members: tuple[str, ...]) -> str:
    """Execution provenance only; never part of task correctness reuse."""

    return _canonical_identity({"schema": "oh-goon.pytest-batch/v1", "members": list(members)})


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name == "posix":
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


class MemberReceiptStore:
    """Exact-context durable proof that one deterministic pytest member completed."""

    def __init__(self, directory: Path, *, suite_identity: str) -> None:
        self.directory = directory
        self.suite_identity = suite_identity

    def _path(self, member: str) -> Path:
        from oh_goon.repository_tooling.pytest_member_identity import pytest_member_identity

        identity = pytest_member_identity(member).removeprefix("sha256:")
        return self.directory / "members" / f"{identity}.json"

    def reusable(self, member: str) -> bool:
        from oh_goon.repository_tooling.pytest_member_identity import pytest_member_identity

        path = self._path(member)
        try:
            payload: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        return (
            payload.get("schema") == RECEIPT_SCHEMA
            and payload.get("suite_identity") == self.suite_identity
            and payload.get("member") == member
            and payload.get("member_identity") == pytest_member_identity(member)
            and payload.get("status") == "PASSED"
        )

    def record(
        self,
        member: str,
        *,
        outcome: str,
        execution_plan_identity: str | None = None,
        observed_batch_identity: str | None = None,
    ) -> Path:
        if outcome not in {"PYTEST_MEMBER_PASSED", "BATCH_PASSED"}:
            raise ValueError(f"unsupported durable pytest receipt outcome: {outcome}")
        from oh_goon.repository_tooling.pytest_member_identity import pytest_member_identity

        path = self._path(member)
        _atomic_json_write(
            path,
            {
                "schema": RECEIPT_SCHEMA,
                "suite_identity": self.suite_identity,
                "member": member,
                "member_identity": pytest_member_identity(member),
                "status": "PASSED",
                "outcome": outcome,
                "execution_plan_identity": execution_plan_identity,
                "observed_batch_identity": observed_batch_identity,
                "completed_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        return path
