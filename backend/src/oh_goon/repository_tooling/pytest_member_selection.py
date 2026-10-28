"""Capture native pytest collection; this owner never interprets Python or markers."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.pytest_process_environment import (
    PytestInvocation,
    resolve_pytest_invocation,
)

_COLLECTION_ENV = "OH_GOON_PYTEST_COLLECTION_OUTPUT"
_DESELECTED: list[dict[str, str]] = []


def collected_item(item: Any, root: Path) -> dict[str, str]:
    """Transport native addressing, including class and parametrized item suffixes."""
    path = Path(item.path).resolve()
    file = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
    suffix = item.nodeid.partition("::")[2]
    return {"file": file, "selector": file + ("::" + suffix if suffix else "")}


def pytest_deselected(items: list[Any]) -> None:
    if os.environ.get(_COLLECTION_ENV):
        _DESELECTED.extend(collected_item(item, Path.cwd().resolve()) for item in items)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    destination = os.environ.get(_COLLECTION_ENV)
    if not destination or exitstatus not in (0, 5):
        return
    write_native_collection(
        Path(destination),
        {
            "selected": [collected_item(item, Path.cwd().resolve()) for item in session.items],
            "deselected": _DESELECTED,
        },
    )


def read_native_collection(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Native pytest collection exceeds the evidence byte budget")
    payload: object = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Native pytest collection must contain a JSON object")
    return payload


def write_native_collection(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".collection-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class NativePytestCollection:
    selected: tuple[dict[str, str], ...]
    deselected: tuple[dict[str, str], ...]

    def groups(self, granularity: str) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for item in self.selected:
            member = item["file"] if granularity == "files" else item["selector"]
            grouped.setdefault(member, []).append(item["selector"])
        return {member: tuple(nodes) for member, nodes in grouped.items()}

    def payload(self) -> dict[str, Any]:
        return {"selected": list(self.selected), "deselected": list(self.deselected)}


def collect_native_pytest(
    paths: tuple[Path, ...],
    *,
    excludes: tuple[str, ...] = (),
    marker: str | None = None,
    timeout: float = 120,
    invocation: PytestInvocation | None = None,
    environment: Mapping[str, str] | None = None,
) -> NativePytestCollection:
    """Run native collection with exactly the execution configuration/environment."""
    with tempfile.TemporaryDirectory(prefix="oh-goon-pytest-collection-") as directory:
        output = Path(directory) / "collection.json"
        invocation = invocation or resolve_pytest_invocation()
        child_environment = dict(invocation.environment if environment is None else environment)
        child_environment[_COLLECTION_ENV] = str(output)
        command = [
            *invocation.command_prefix,
            "--collect-only",
            "-qq",
            "-c",
            str(invocation.config_file),
            "-p",
            "oh_goon.repository_tooling.pytest_member_selection",
            *invocation.pytest_args,
        ]
        for excluded in excludes:
            command += ["--ignore", excluded]
        if marker:
            command += ["-m", marker]
        command += [str(path) for path in paths]
        try:
            result = run_command(
                *command,
                cwd=invocation.cwd,
                env=child_environment,
                timeout=timeout,
                merge_stderr=True,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                retain_output_tail=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Native pytest collection timed out") from exc
        if result.returncode not in (0, 5) or not output.is_file():
            evidence = result.stdout_evidence
            observed = (
                f" raw-bytes={evidence.byte_count} sha256={evidence.sha256}"
                if evidence is not None
                else ""
            )
            raise ValueError(
                f"Native pytest collection failed ({result.returncode}); "
                f"output-truncated={result.stdout_truncated}{observed}: {result.stdout}"
            )
        packet = read_native_collection(output)
    collection = NativePytestCollection(tuple(packet["selected"]), tuple(packet["deselected"]))
    selectors = [item["selector"] for item in collection.selected]
    if len(selectors) != len(set(selectors)):
        raise ValueError("Native pytest collection contains duplicate executable targets")
    return collection
