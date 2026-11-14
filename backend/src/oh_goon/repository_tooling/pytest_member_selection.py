"""Transport native pytest addressing and explicitly declared reuse eligibility."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oh_goon.repository_tooling.pytest_process_environment import PytestInvocation

_COLLECTION_ENV = "OH_GOON_PYTEST_COLLECTION_OUTPUT"
_COLLECTION_ENVIRONMENT = "OH_GOON_PYTEST_EXPECTED_ENVIRONMENT"
_DESELECTED: list[dict[str, str]] = []
_PROJECT_EXECUTION_ENV = "OH_GOON_PYTEST_PROJECT_EXECUTION_OUTPUT"
_PROJECT_ADMISSION_ENV = "OH_GOON_PYTEST_PROJECT_ADMISSION"
_PROJECT_OUTPUT: Path | None = None
_PROJECT_EXPECTED: tuple[str, ...] | None = None
_PROJECT_MARKER_ENV = "OH_GOON_PYTEST_PROJECT_MARKER"
_PROJECT_MARKER: str | None = None
_PROJECT_STARTED: list[str] = []
_PROJECT_PHASES: list[dict[str, Any]] = []
_PROJECT_OBSERVATION_BYTES = 0


def register_resume_marker(config: Any) -> None:
    config.addinivalue_line(
        "markers", "no_resume: external state has no cross-invocation reusable PASS authority"
    )


def pytest_configure(config: Any) -> None:
    register_resume_marker(config)
    if _PROJECT_MARKER is not None:
        native = config.option.markexpr
        config.option.markexpr = (
            f"({native}) and ({_PROJECT_MARKER})" if native else _PROJECT_MARKER
        )


def collected_item(item: Any, root: Path) -> dict[str, str]:
    """Transport native addressing, including class and parametrized item suffixes."""
    path = Path(item.path).resolve()
    file = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
    suffix = item.nodeid.partition("::")[2]
    row = {"file": file, "selector": file + ("::" + suffix if suffix else "")}
    if item.get_closest_marker("no_resume") is not None:
        row["no_resume"] = "true"
    if item.get_closest_marker("native_resources") is not None:
        row["native_resources"] = "true"
    if "native_frontend_toolchain" in getattr(item, "fixturenames", ()):
        row["native_frontend"] = "true"
    return row


def _bound_project_observation(value: Any) -> None:
    global _PROJECT_OBSERVATION_BYTES
    _PROJECT_OBSERVATION_BYTES += len(json.dumps(value, ensure_ascii=True)) + 2
    if _PROJECT_OBSERVATION_BYTES > 16 * 1024 * 1024 - 4096:
        import pytest

        pytest.exit("Native project observation exceeds its transport bound", returncode=2)


def pytest_deselected(items: list[Any]) -> None:
    if os.environ.get(_COLLECTION_ENV) or _PROJECT_OUTPUT is not None:
        for item in items:
            row = collected_item(item, Path.cwd().resolve())
            if _PROJECT_OUTPUT is not None:
                _bound_project_observation(row)
            _DESELECTED.append(row)


def pytest_collection_finish(session: Any) -> None:
    if _PROJECT_EXPECTED is None:
        return
    import pytest

    actual = []
    for item in session.items:
        row = {"nodeid": item.nodeid, **collected_item(item, Path.cwd().resolve())}
        _bound_project_observation(row)
        actual.append(row["selector"])
    if len(actual) != len(set(actual)) or set(actual) != set(_PROJECT_EXPECTED):
        raise pytest.UsageError("Native project collection changed after admission")


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    if _PROJECT_OUTPUT is not None:
        _bound_project_observation(nodeid)
        _PROJECT_STARTED.append(nodeid)


def pytest_runtest_logreport(report: Any) -> None:
    if _PROJECT_OUTPUT is None:
        return
    # Transport native observations only. The existing controller's receipt
    # owner retains final category and exact membership authority.
    phase = {
        "nodeid": report.nodeid,
        "when": report.when,
        "outcome": report.outcome,
        "failed": report.failed,
        "skipped": report.skipped,
        "wasxfail": True if getattr(report, "wasxfail", None) is not None else None,
    }
    _bound_project_observation(phase)
    _PROJECT_PHASES.append(phase)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    import pytest

    if _PROJECT_OUTPUT is not None:
        write_native_collection(
            _PROJECT_OUTPUT,
            {
                "items": [
                    {"nodeid": item.nodeid, **collected_item(item, Path.cwd().resolve())}
                    for item in session.items
                ],
                "deselected": _DESELECTED,
                "started": _PROJECT_STARTED,
                "phases": _PROJECT_PHASES,
                "exitstatus": int(exitstatus),
                "runtime": {
                    "executable": sys.executable,
                    "prefix": sys.prefix,
                    "collector": __file__,
                    "python_version": sys.version,
                    "pytest_version": pytest.__version__,
                    "additional_marker": _PROJECT_MARKER,
                    "marker_expression": session.config.option.markexpr,
                },
            },
        )
    destination = os.environ.get(_COLLECTION_ENV)
    if not destination or exitstatus not in (0, 5):
        return
    write_native_collection(
        Path(destination),
        {
            "selected": [collected_item(item, Path.cwd().resolve()) for item in session.items],
            "deselected": _DESELECTED,
            "runtime": {
                "executable": sys.executable,
                "prefix": sys.prefix,
                "collector": __file__,
                "python_version": sys.version,
                "pytest_version": pytest.__version__,
                "additional_marker": _PROJECT_MARKER,
                "marker_expression": session.config.option.markexpr,
            },
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
    request: Mapping[str, Any] | None = None
    runtime: Mapping[str, str] | None = None

    def groups(self, granularity: str) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for item in self.selected:
            member = item["file"] if granularity == "files" else item["selector"]
            grouped.setdefault(member, []).append(item["selector"])
        return {member: tuple(nodes) for member, nodes in grouped.items()}

    def payload(self) -> dict[str, Any]:
        return {"selected": list(self.selected), "deselected": list(self.deselected)}

    def relative_to_source(self, *, cwd: Path, source_root: Path) -> NativePytestCollection:
        """Transport native addresses without changing their item suffixes."""

        def rebase(row: dict[str, str]) -> dict[str, str]:
            original = row["file"]
            member = (cwd / original).resolve()
            if not member.is_relative_to(source_root):
                raise ValueError("Native ordinary collection escaped its captured source")
            selector = row["selector"]
            if selector != original and not selector.startswith(original + "::"):
                raise ValueError("Native collection selector does not match its file")
            relative = member.relative_to(source_root).as_posix()
            return {**row, "file": relative, "selector": relative + selector[len(original) :]}

        return NativePytestCollection(
            tuple(rebase(row) for row in self.selected),
            tuple(rebase(row) for row in self.deselected),
            self.request,
            self.runtime,
        )


def native_pytest_address(root: Path, selector: str) -> str:
    """Resolve the file while preserving pytest's opaque class/parameter suffix."""
    filename, separator, suffix = selector.partition("::")
    return str(root / filename) + separator + suffix


def native_collection_request(
    paths: tuple[Path | str, ...],
    *,
    invocation: PytestInvocation,
    excludes: tuple[str, ...] = (),
    marker: str | None = None,
    plugin_module: str = "oh_goon.repository_tooling.pytest_member_selection",
    expected_environment: Path | None = None,
    additional_marker: str | None = None,
) -> dict[str, Any]:
    """Resolve one exact native collection request without executing it."""
    command = [
        *invocation.command_prefix,
        "--collect-only",
        "-qq",
        "-c",
        str(invocation.config_file),
        "-p",
        plugin_module,
        *invocation.pytest_args,
    ]
    for excluded in excludes:
        command += ["--ignore", excluded]
    if marker:
        command += ["-m", marker]
    command += [str(path) for path in paths]
    return {
        "cwd": str(invocation.cwd),
        "source_root": str(invocation.source_root),
        "config_file": str(invocation.config_file),
        "config_identity": invocation.config_identity,
        "correctness_context": invocation.correctness_context(),
        "command_prefix": list(invocation.command_prefix),
        "pytest_args": list(invocation.pytest_args),
        "qualified": invocation.qualified,
        "resume_environment": list(invocation.resume_environment),
        "argv": command,
        "paths": [str(path) for path in paths],
        "excludes": list(excludes),
        "marker": marker,
        **({"additional_marker": additional_marker} if additional_marker is not None else {}),
        "plugin_module": plugin_module,
        "expected_environment": str(expected_environment)
        if expected_environment is not None
        else None,
    }


def collect_native_pytest(
    paths: tuple[Path | str, ...],
    *,
    excludes: tuple[str, ...] = (),
    marker: str | None = None,
    timeout: float = 120,
    invocation: PytestInvocation | None = None,
    environment: Mapping[str, str] | None = None,
    plugin_module: str = "oh_goon.repository_tooling.pytest_member_selection",
    expected_environment: Path | None = None,
    additional_marker: str | None = None,
) -> NativePytestCollection:
    """Run native collection with exactly the execution configuration/environment."""
    from oh_goon.engine.command import run_command
    from oh_goon.repository_tooling.pytest_process_environment import resolve_pytest_invocation

    with tempfile.TemporaryDirectory(prefix="oh-goon-pytest-collection-") as directory:
        output = Path(directory) / "collection.json"
        invocation = invocation or resolve_pytest_invocation()
        if environment is not None:
            invocation = replace(invocation, environment=MappingProxyType(dict(environment)))
        child_environment = dict(invocation.environment)
        child_environment.pop(_PROJECT_MARKER_ENV, None)
        if additional_marker is not None:
            if plugin_module != "__main__":
                raise ValueError("Additional project markers require native collector hooks")
            child_environment[_PROJECT_MARKER_ENV] = additional_marker
        child_environment[_COLLECTION_ENV] = str(output)
        if expected_environment is not None:
            child_environment[_COLLECTION_ENVIRONMENT] = str(expected_environment)
        request = native_collection_request(
            paths,
            invocation=invocation,
            excludes=excludes,
            marker=marker,
            plugin_module=plugin_module,
            expected_environment=expected_environment,
            additional_marker=additional_marker,
        )
        command = request["argv"]
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
        if (
            result.returncode not in (0, 5)
            or not output.is_file()
            or result.supervisor is None
            or result.supervisor.final_process_group_quiescent is not True
        ):
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
        if expected_environment is not None:
            runtime = packet.get("runtime", {})
            if (
                Path(runtime.get("prefix", "")).resolve() != expected_environment.resolve()
                or Path(runtime.get("executable", "")).absolute()
                != expected_environment / "bin/python"
            ):
                raise ValueError("Native collection changed its selected Python environment")
            if (
                plugin_module == "__main__"
                and Path(runtime.get("collector", "")).resolve()
                != Path(invocation.command_prefix[-1]).resolve()
            ):
                raise ValueError("Native collection did not retain its trusted collector hooks")
    collection = NativePytestCollection(
        tuple(packet["selected"]),
        tuple(packet["deselected"]),
        request,
        packet.get("runtime"),
    )
    selectors = [item["selector"] for item in collection.selected]
    if len(selectors) != len(set(selectors)):
        raise ValueError("Native pytest collection contains duplicate executable targets")
    return collection


if __name__ == "__main__":
    # The immutable merge controller can execute this exact collector file with
    # candidate-native Python/pytest while -p __main__ retains these hook bytes.
    selected_environment = os.environ.get(_COLLECTION_ENVIRONMENT)
    if selected_environment and Path(sys.prefix).resolve() != Path(selected_environment).resolve():
        raise SystemExit("Native collection Python environment does not match its authority")
    _PROJECT_MARKER = os.environ.pop(_PROJECT_MARKER_ENV, None)
    project_output = os.environ.pop(_PROJECT_EXECUTION_ENV, None)
    project_admission = os.environ.pop(_PROJECT_ADMISSION_ENV, None)
    if (project_output is None) != (project_admission is None):
        raise SystemExit("Native project observations require their exact admission")
    if project_output is not None:
        assert project_admission is not None
        _PROJECT_OUTPUT = Path(project_output)
        admitted = read_native_collection(Path(project_admission))
        if admitted.get("additional_marker") != _PROJECT_MARKER:
            raise SystemExit("Native project marker does not match its authority")
        expected = admitted["selected"]
        if (
            not isinstance(expected, list)
            or not expected
            or any(not isinstance(item, str) for item in expected)
            or len(expected) != len(set(expected))
        ):
            raise SystemExit("Native project admission has invalid membership")
        _PROJECT_EXPECTED = tuple(expected)
    import pytest

    raise SystemExit(pytest.main(sys.argv[1:]))
