"""Run the shipped Python projects' native test loops in owned environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Any

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    observe_command_processes,
    run_command,
)
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.integrations.python_goon_projects import (
    capture_project_source,
    materialize_python_goon_project,
)
from oh_goon.repository_tooling.pytest_execution_report import (
    _admit_report_directory_location,
    _open_report_directory,
)
from oh_goon.repository_tooling.pytest_process_environment import without_test_report_environment
from oh_goon.runtime.template_discovery import discover_template_packages

if TYPE_CHECKING:
    from oh_goon.repository_tooling.pytest_process_environment import PytestInvocation

ROOT = Path(__file__).resolve().parents[4]
_PROJECTS = (
    ("iris", "templates/goons/ml/iris-train", ("tests",)),
    (
        "orders",
        "templates/goons/general/orders-taskflow",
        ("tests",),
    ),
)
PROJECT_REPORT_SCHEMA = "oh-goon.python-project-checks/v1"
NATIVE_PROJECT_OWNER = "Native project pytest"


def project_for_pytest_target(root: Path, target: str) -> str | None:
    """Select only an existing configured owner's execution context, never tests."""
    filename = target.partition("::")[0]
    path = Path(filename)
    if (
        not filename
        or path.is_absolute()
        or path.as_posix() != filename
        or ".." in path.parts
        or "\\" in filename
        or "\n" in filename
    ):
        return None
    for _name, relative, tests in _PROJECTS:
        for test in tests:
            owned = Path(relative) / test
            if path == owned or path.is_relative_to(owned):
                return relative
    return None


def configured_project_pytest_targets(root: Path, project: str) -> tuple[str, ...]:
    for _name, relative, tests in _PROJECTS:
        if relative == project:
            targets = tuple((Path(relative) / test).as_posix() for test in tests)
            if all(project_for_pytest_target(root, target) == relative for target in targets):
                return targets
    raise ValueError("Native pytest project context is not configured")


def _project_context(
    root: Path, project: str, marker: str | None, source_identity: str
) -> dict[str, Any]:
    from oh_goon.core.file_digest import sha256_file
    from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

    configured_project_pytest_targets(root, project)
    if marker is not None and (not isinstance(marker, str) or not marker):
        raise ValueError("Native project marker context is invalid")
    if canonical_source_identity(root) != source_identity:
        raise ValueError("Native project source changed after admission")
    directory = root / project
    if not directory.is_dir() or directory.resolve(strict=True) != directory:
        raise ValueError("Native project context has an aliased root")
    for name in ("pyproject.toml", "uv.lock"):
        path = directory / name
        if not path.is_file() or path.resolve(strict=True) != path:
            raise ValueError("Native project authority must be regular source files")
    return {
        "project": project,
        "pyproject_sha256": sha256_file(directory / "pyproject.toml"),
        "uv_lock_sha256": sha256_file(directory / "uv.lock"),
        "collector_sha256": sha256_file(Path(__file__).with_name("pytest_member_selection.py")),
        "group": "dev",
        "marker": marker,
        "source_identity": source_identity,
    }


@contextmanager
def _project_session(
    root: Path, project: str, marker: str | None, source_identity: str
) -> Iterator[dict[str, Any]]:
    root = root.resolve(strict=True)
    context = _project_context(root, project, marker, source_identity)
    scratch = Path(tempfile.mkdtemp(prefix="oh-goon-native-project-"))
    quiescent = True
    failure: BaseException | None = None
    session: dict[str, Any] = {
        "context": context,
        "scratch": scratch,
        "cleanup": "INCOMPLETE",
        "native_execution": [],
    }

    def observe(event: CommandProcessLifecycleEvent) -> None:
        nonlocal quiescent
        if event.kind == "PROCESS_STARTING":
            quiescent = False
        elif event.kind == "DESCENDANTS_QUIESCENT":
            quiescent = True

    try:
        if scratch.resolve().is_relative_to(root):
            raise ValueError("Native project environment must remain outside source")
        environment = scratch / "environment"
        child_env = without_test_report_environment(os.environ)
        for name in (
            "OH_GOON_PYTEST_PROJECT_EXECUTION_OUTPUT",
            "OH_GOON_PYTEST_PROJECT_ADMISSION",
            "OH_GOON_PYTEST_COLLECTION_OUTPUT",
            "OH_GOON_PYTEST_EXPECTED_ENVIRONMENT",
            "OH_GOON_PYTEST_PROJECT_MARKER",
        ):
            child_env.pop(name, None)
        child_env.update(UV_PROJECT_ENVIRONMENT=str(environment), PYTHONDONTWRITEBYTECODE="1")
        with observe_command_processes(observe):
            sync = run_command(
                *_project_prepare_command(root, project),
                cwd=root,
                env=child_env,
                timeout=180,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
            )
            session["native_execution"].append(
                {
                    "phase": "prepare",
                    "argv": list(sync.argv),
                    "returncode": sync.returncode,
                    "process_group_quiescent": sync.supervisor.final_process_group_quiescent,
                }
            )
            if (
                sync.returncode
                or not quiescent
                or not sync.supervisor.final_process_group_quiescent
            ):
                raise ValueError("Native project preparation failed or process cleanup is unproven")
            collector = Path(__file__).with_name("pytest_member_selection.py").resolve()
            invocation = _project_invocation(root, context, environment, child_env)
            session.update(
                environment=environment,
                invocation=invocation,
                collector=collector,
                child_environment=child_env,
            )
            yield session
            if context != _project_context(root, project, marker, source_identity):
                raise ValueError("Native project context changed during verification")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if quiescent:
            try:
                shutil.rmtree(scratch)
                session["cleanup"] = "VERIFIED"
            except OSError as exc:
                if failure is None:
                    raise
                failure.add_note(f"Native project cleanup failed: {exc}")
        else:
            message = f"Native project environment retained: process cleanup unproven: {scratch}"
            if failure is not None:
                failure.add_note(message)
            else:
                raise RuntimeError(message)


def _project_invocation(
    root: Path,
    context: Mapping[str, Any],
    environment: Path,
    child_env: Mapping[str, str],
    *,
    collector: Path | None = None,
) -> PytestInvocation:
    from oh_goon.repository_tooling.pytest_process_environment import PytestInvocation

    collector = collector or Path(__file__).with_name("pytest_member_selection.py").resolve()
    return PytestInvocation(
        cwd=root,
        source_root=root,
        config_file=root / context["project"] / "pyproject.toml",
        pytest_args=("-p", "no:cacheprovider"),
        command_prefix=(str(environment / "bin/python"), "-I", "-B", str(collector)),
        environment=MappingProxyType(child_env),
        qualified=True,
        config_identity=context["pyproject_sha256"],
    )


def _validated_project_collector(root: Path, context: Mapping[str, Any], value: Any) -> Path:
    from oh_goon.core.file_digest import sha256_file

    if not isinstance(value, str):
        raise ValueError("Native project collector is unavailable")
    collector = Path(value)
    allowed = {
        Path(__file__).with_name("pytest_member_selection.py"),
        root / "backend/src/oh_goon/repository_tooling/pytest_member_selection.py",
    }
    if (
        collector not in allowed
        or not collector.is_file()
        or collector.resolve(strict=True) != collector
        or sha256_file(collector) != context["collector_sha256"]
    ):
        raise ValueError("Native project collector bytes or location changed")
    return collector


def _project_prepare_command(root: Path, project: str) -> tuple[str, ...]:
    return (
        "uv",
        "sync",
        "--project",
        str(root / project),
        "--frozen",
        "--no-default-groups",
        "--group",
        "dev",
        "--no-python-downloads",
    )


def _project_execution_command(
    root: Path,
    context: Mapping[str, Any],
    environment: Path,
    selected: Sequence[str],
    *,
    collector: Path | None = None,
) -> list[str]:
    from oh_goon.repository_tooling.pytest_member_selection import native_pytest_address

    command = [
        *_project_invocation(root, context, environment, {}, collector=collector).command_prefix,
        "-c",
        str(root / context["project"] / "pyproject.toml"),
        "-p",
        "__main__",
        "-p",
        "no:cacheprovider",
    ]
    command.extend(native_pytest_address(root, member) for member in selected)
    return command


def _require_native_execution(
    value: Any, phases: tuple[str, ...], commands: Sequence[Sequence[str]]
) -> None:
    if (
        not isinstance(value, list)
        or len(value) != len(phases)
        or any(
            not isinstance(row, Mapping)
            or row.get("phase") != phase
            or row.get("argv") != list(command)
            or row.get("process_group_quiescent") is not True
            or type(row.get("returncode")) is not int
            or row["returncode"] != 0
            for row, phase, command in zip(value, phases, commands, strict=True)
        )
    ):
        raise ValueError("Native project process cleanup or outcome is unproven")


def _runtime_identity(
    runtime: Mapping[str, Any], environment: Path, collector: Path
) -> dict[str, str]:
    from oh_goon.core.file_digest import sha256_file

    if (
        Path(str(runtime.get("prefix", ""))) != environment
        or Path(str(runtime.get("executable", ""))) != environment / "bin/python"
        or Path(str(runtime.get("collector", ""))).resolve() != collector
    ):
        raise ValueError("Native project collection used another interpreter or collector")
    return {
        "python_version": str(runtime["python_version"]),
        "pytest_version": str(runtime["pytest_version"]),
        "interpreter_sha256": sha256_file((environment / "bin/python").resolve(strict=True)),
    }


def collect_project_pytest(
    root: Path, targets: Sequence[str], *, marker: str | None, source_identity: str
) -> list[dict[str, Any]]:
    from oh_goon.repository_tooling.pytest_member_selection import (
        collect_native_pytest,
        native_pytest_address,
    )

    root = root.resolve(strict=True)
    grouped: dict[str, list[str]] = {}
    for target in targets:
        project = project_for_pytest_target(root, target)
        if project is None:
            raise ValueError("Native pytest target has no configured project context")
        grouped.setdefault(project, []).append(target)
    packets: list[dict[str, Any]] = []
    for project, selected in sorted(grouped.items()):
        with _project_session(root, project, marker, source_identity) as session:
            collection = collect_native_pytest(
                tuple(native_pytest_address(root, target) for target in sorted(set(selected))),
                additional_marker=marker,
                invocation=session["invocation"],
                plugin_module="__main__",
                expected_environment=session["environment"],
            )
            packet = {
                "project": project,
                "context": session["context"],
                "collection": collection.payload(),
                "runtime": dict(collection.runtime or {}),
                "request": dict(collection.request or {}),
                "environment": str(session["environment"]),
                "native_execution": session["native_execution"],
            }
            packet["runtime_identity"] = _runtime_identity(
                packet["runtime"], session["environment"], session["collector"]
            )
        packet["cleanup"] = session["cleanup"]
        packets.append(packet)
    return packets


def require_project_pytest_admission(
    root: Path, admissions: Sequence[Mapping[str, Any]], *, source_identity: str
) -> set[str]:
    root = root.resolve(strict=True)
    members: set[str] = set()
    projects: set[str] = set()
    for packet in admissions:
        project, context = packet.get("project"), packet.get("context")
        if (
            not isinstance(project, str)
            or not isinstance(context, Mapping)
            or project in projects
            or packet.get("cleanup") != "VERIFIED"
        ):
            raise ValueError("Native project admission has invalid or duplicate context")
        if context != _project_context(root, project, context.get("marker"), source_identity):
            raise ValueError("Native project admission is stale or has changed context")
        projects.add(project)
        collection = packet.get("collection", {})
        if not isinstance(collection, Mapping):
            raise ValueError("Native project collection is malformed")
        selected, deselected = collection.get("selected", []), collection.get("deselected", [])
        if not isinstance(selected, list) or not selected or not isinstance(deselected, list):
            raise ValueError("Native project admission has no exact selected membership")
        seen: set[str] = set()
        for row in (*selected, *deselected):
            if (
                not isinstance(row, Mapping)
                or not isinstance(row.get("selector"), str)
                or not isinstance(row.get("file"), str)
                or row["selector"].split("::", 1)[0] != row["file"]
                or project_for_pytest_target(root, row["selector"]) != project
                or row["selector"] in seen
            ):
                raise ValueError("Native project collection is duplicated or outside its context")
            seen.add(row["selector"])
        current = {row["selector"] for row in selected}
        if members & current:
            raise ValueError("Native project admission duplicates an executable member")
        members.update(current)
        from oh_goon.repository_tooling.pytest_member_selection import native_collection_request

        runtime, request = packet.get("runtime", {}), packet.get("request", {})
        identity = packet.get("runtime_identity", {})
        environment = Path(str(packet.get("environment", "")))
        if not all(isinstance(row, Mapping) for row in (runtime, request, identity)):
            raise ValueError("Native project native authority is malformed")
        paths = request.get("paths")
        if (
            environment.is_relative_to(root)
            or not environment.is_absolute()
            or identity.get("python_version") != runtime.get("python_version")
            or identity.get("pytest_version") != runtime.get("pytest_version")
            or runtime.get("additional_marker") != context["marker"]
            or not isinstance(runtime.get("marker_expression"), str)
            or not isinstance(identity.get("interpreter_sha256"), str)
            or len(identity["interpreter_sha256"]) != 64
            or Path(str(runtime.get("prefix", ""))) != environment
            or Path(str(runtime.get("executable", ""))) != environment / "bin/python"
            or not isinstance(paths, list)
            or not paths
        ):
            raise ValueError("Native project admission lost its native execution authority")
        for path in paths:
            if not isinstance(path, str):
                raise ValueError("Native project collection addressing is malformed")
            filename, separator, suffix = path.partition("::")
            file = Path(filename)
            if not file.is_relative_to(root) or str(file) != filename:
                raise ValueError("Native project collection file escaped its context")
            selector = file.relative_to(root).as_posix() + separator + suffix
            if project_for_pytest_target(root, selector) != project:
                raise ValueError("Native project collection file changed its context")
        collector = _validated_project_collector(root, context, runtime.get("collector"))
        invocation = _project_invocation(
            root, context, environment, os.environ, collector=collector
        )
        resolved_request = native_collection_request(
            tuple(paths),
            invocation=invocation,
            additional_marker=context["marker"],
            plugin_module="__main__",
            expected_environment=environment,
        )
        correctness = request.get("correctness_context")
        if (
            not isinstance(correctness, str)
            or not correctness.startswith("native-pytest-invocation:")
            or len(correctness.removeprefix("native-pytest-invocation:")) != 64
        ):
            raise ValueError("Native project correctness context is unavailable")
        # This identity was resolved by the original native collection owner.
        # A later trusted controller must not reinterpret another process's
        # PATH/HOME/plugin inputs using its own ambient defaults.
        resolved_request["correctness_context"] = correctness
        if request != resolved_request:
            raise ValueError("Native project collection request changed after admission")
        _require_native_execution(
            packet.get("native_execution"), ("prepare",), (_project_prepare_command(root, project),)
        )
    return members


def _project_native_report(
    raw: Mapping[str, Any],
    selected: tuple[str, ...],
    directory: Path,
    *,
    source_identity: str,
    returncode: int,
) -> dict[str, Any]:
    from oh_goon.repository_tooling import pytest_batch_receipts as owner
    from oh_goon.repository_tooling.pytest_execution_membership import (
        ExecutionMembershipStore,
        execution_attempt_identity,
        execution_run_identity,
        logical_membership_identity,
    )
    from oh_goon.repository_tooling.pytest_execution_report import execution_report_schema
    from oh_goon.repository_tooling.pytest_member_selection import read_native_collection

    identity = logical_membership_identity(selected)
    run = execution_run_identity(suite_identity=source_identity, execution_plan_identity=identity)
    attempt = execution_attempt_identity(
        run_identity=run,
        root_batch_identity=identity,
        root_attempt=0,
        launch_ordinal=0,
        members=selected,
    )
    store = ExecutionMembershipStore(
        directory,
        suite_identity=source_identity,
        execution_plan_identity=identity,
        run_identity=run,
    )
    items = raw.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Native project execution collection is malformed")
    if any(
        not isinstance(item.get("nodeid"), str)
        or not isinstance(item.get("selector"), str)
        or not isinstance(item.get("file"), str)
        or item["selector"].partition("::")[0] != item["file"]
        for item in items
    ):
        raise ValueError("Native project execution addressing is malformed")
    by_node = {item["nodeid"]: item["selector"] for item in items}
    if len(by_node) != len(items) or len(set(by_node.values())) != len(items):
        raise ValueError("Native project execution collection has duplicate members")
    states: dict[str, dict[str, Any]] = {}
    observed: dict[str, set[str]] = {}
    previous_states = {node: owner._ITEM_STATE.get(node) for node in by_node}
    previous_outcomes = {node: owner._ITEM_OUTCOME.get(node) for node in by_node}
    try:
        for node, member in by_node.items():
            owner._ITEM_STATE.pop(node, None)
            owner._ITEM_OUTCOME.pop(node, None)
            if member not in selected:
                store.record_extra(node, attempt_identity=attempt)
        started = raw.get("started", [])
        if (
            not isinstance(started, list)
            or any(not isinstance(node, str) for node in started)
            or len(started) != len(set(started))
        ):
            raise ValueError("Native project start observations are malformed or duplicated")
        for node in started:
            if node not in by_node:
                raise ValueError("Native project started an uncollected item")
            store.record_started(
                by_node[node], attempt_identity=attempt, observed_batch_identity=identity
            )
            states[node] = {"failed": False, "non_passed": False}
        phases = raw.get("phases", [])
        if not isinstance(phases, list):
            raise ValueError("Native project phase observations are malformed")
        for phase in phases:
            if (
                not isinstance(phase, dict)
                or phase.get("nodeid") not in states
                or phase.get("when") not in {"setup", "call", "teardown"}
                or phase.get("outcome") not in {"passed", "failed", "skipped"}
                or phase.get("failed") is not (phase["outcome"] == "failed")
                or phase.get("skipped") is not (phase["outcome"] == "skipped")
                or (phase.get("wasxfail") is not None and phase.get("wasxfail") is not True)
            ):
                raise ValueError("Native project phase observations are malformed")
            node, when = phase["nodeid"], phase["when"]
            seen = observed.setdefault(node, set())
            if (
                when in seen
                or "teardown" in seen
                or (when != "setup" and "setup" not in seen)
                or (when == "setup" and seen)
            ):
                raise ValueError("Native project phase observations are duplicated")
            seen.add(when)
            owner._observe_report(SimpleNamespace(**phase), states[node])
            if when == "teardown":
                store.record_terminal(
                    by_node[node],
                    attempt_identity=attempt,
                    observed_batch_identity=identity,
                    status=owner._terminal_status(states[node]),
                    pytest_outcomes=[
                        {"nodeid": node, "outcome": owner._ITEM_OUTCOME.get(node, "UNREPORTED")}
                    ],
                )
        receipt, _path = store.finalize(
            selected=selected, resumed=(), suite_passed=returncode == 0, recovery_lineage=()
        )
    finally:
        for node in by_node:
            owner._ITEM_STATE.pop(node, None)
            owner._ITEM_OUTCOME.pop(node, None)
            previous_state, previous_outcome = previous_states[node], previous_outcomes[node]
            if previous_state is not None:
                owner._ITEM_STATE[node] = previous_state
            if previous_outcome is not None:
                owner._ITEM_OUTCOME[node] = previous_outcome
    actual = [item["selector"] for item in items]
    deselected = [item["selector"] for item in raw.get("deselected", [])]
    return {
        "schema": execution_report_schema(),
        "status": "PASS" if returncode == 0 else "FAIL",
        "source_identity": source_identity,
        "membership_receipt": read_native_collection(_path),
        "native_collection": {"discovered": actual + deselected, "deselected": deselected},
    }


def execute_project_pytest(
    root: Path, admissions: Sequence[Mapping[str, Any]], *, source_identity: str
) -> list[dict[str, Any]]:
    from oh_goon.core.file_digest import sha256_file
    from oh_goon.repository_tooling.pytest_member_selection import (
        read_native_collection,
        write_native_collection,
    )
    from oh_goon.repository_tooling.verification_gate_control import require_native_pytest_pass

    root = root.resolve(strict=True)
    require_project_pytest_admission(root, admissions, source_identity=source_identity)
    outcomes: list[dict[str, Any]] = []
    for packet in admissions:
        project = packet["project"]
        selected = tuple(sorted(row["selector"] for row in packet["collection"]["selected"]))
        with _project_session(
            root, project, packet["context"]["marker"], source_identity
        ) as session:
            interpreter = (session["environment"] / "bin/python").resolve(strict=True)
            if sha256_file(interpreter) != packet["runtime_identity"]["interpreter_sha256"]:
                raise ValueError("Native project interpreter changed before execution")
            admission_file, report_file = (
                session["scratch"] / "admission.json",
                session["scratch"] / "execution.json",
            )
            write_native_collection(
                admission_file,
                {"selected": list(selected), "additional_marker": packet["context"]["marker"]},
            )
            child_env = {
                **session["child_environment"],
                "OH_GOON_PYTEST_PROJECT_EXECUTION_OUTPUT": str(report_file),
                "OH_GOON_PYTEST_PROJECT_ADMISSION": str(admission_file),
                "OH_GOON_PYTEST_EXPECTED_ENVIRONMENT": str(session["environment"]),
            }
            if packet["context"]["marker"] is not None:
                child_env["OH_GOON_PYTEST_PROJECT_MARKER"] = packet["context"]["marker"]
            command = _project_execution_command(
                root, session["context"], session["environment"], selected
            )
            result = run_command(
                *command,
                cwd=root,
                env=child_env,
                timeout=600,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
            )
            session["native_execution"].append(
                {
                    "phase": "execute",
                    "argv": list(result.argv),
                    "returncode": result.returncode,
                    "process_group_quiescent": result.supervisor.final_process_group_quiescent,
                }
            )
            if not result.supervisor.final_process_group_quiescent:
                raise ValueError("Native project execution process cleanup is unproven")
            raw = read_native_collection(report_file)
            runtime = raw["runtime"]
            runtime_identity = _runtime_identity(
                runtime, session["environment"], session["collector"]
            )
            if (
                runtime_identity != packet["runtime_identity"]
                or runtime.get("additional_marker") != packet["context"]["marker"]
                or runtime.get("marker_expression") != packet["runtime"]["marker_expression"]
                or raw.get("exitstatus") != result.returncode
            ):
                raise ValueError("Native project toolchain or exit observation changed")
            native = _project_native_report(
                raw,
                selected,
                session["scratch"] / "receipts",
                source_identity=source_identity,
                returncode=result.returncode,
            )
            try:
                require_native_pytest_pass(native, expected_source=source_identity)
                passed = True
            except ValueError:
                native["status"] = "FAIL"
                passed = False
            outcome = {
                "owner": NATIVE_PROJECT_OWNER,
                "project": project,
                "context": session["context"],
                "returncode": result.returncode or (0 if passed else 1),
                "native_report": native,
                "native_execution": session["native_execution"],
                "runtime": runtime,
                "runtime_identity": runtime_identity,
                "environment": str(session["environment"]),
            }
            if not passed or result.returncode:
                outcome["native_diagnostic"] = {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "stdout_truncated": result.stdout_truncated,
                }
        outcome["cleanup"] = session["cleanup"]
        outcomes.append(outcome)
    return outcomes


def require_project_pytest_outcomes(
    root: Path,
    admissions: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    source_identity: str,
) -> tuple[set[str], set[str]]:
    from oh_goon.repository_tooling.verification_gate_control import require_native_pytest_pass

    root = root.resolve(strict=True)
    require_project_pytest_admission(root, admissions, source_identity=source_identity)
    expected = {packet["project"]: packet for packet in admissions}
    if len(outcomes) != len(expected):
        raise ValueError("Native project owners are missing or extra")
    files: set[str] = set()
    members: set[str] = set()
    seen: set[str] = set()
    for outcome in outcomes:
        project = outcome.get("project")
        if (
            not isinstance(project, str)
            or project not in expected
            or project in seen
            or outcome.get("owner") != NATIVE_PROJECT_OWNER
            or outcome.get("returncode") != 0
            or outcome.get("cleanup") != "VERIFIED"
        ):
            raise ValueError("Native project owner is missing, duplicated or non-PASS")
        packet = expected[project]
        environment = Path(str(outcome.get("environment", "")))
        runtime = outcome.get("runtime", {})
        if not isinstance(runtime, Mapping):
            raise ValueError("Native project runtime is malformed")
        if (
            outcome.get("context") != packet["context"]
            or outcome.get("runtime_identity") != packet["runtime_identity"]
            or not environment.is_absolute()
            or environment.is_relative_to(root)
            or runtime.get("prefix") != str(environment)
            or runtime.get("executable") != str(environment / "bin/python")
            or runtime.get("additional_marker") != packet["context"]["marker"]
            or runtime.get("marker_expression") != packet["runtime"]["marker_expression"]
        ):
            raise ValueError("Native project execution context or process cleanup changed")
        collector = _validated_project_collector(root, packet["context"], runtime.get("collector"))
        _require_native_execution(
            outcome.get("native_execution"),
            ("prepare", "execute"),
            (
                _project_prepare_command(root, project),
                _project_execution_command(
                    root,
                    packet["context"],
                    environment,
                    sorted(row["selector"] for row in packet["collection"]["selected"]),
                    collector=collector,
                ),
            ),
        )
        native = outcome.get("native_report", {})
        if not isinstance(native, Mapping):
            raise ValueError("Native project report is malformed")
        found = require_native_pytest_pass(native, expected_source=source_identity)
        executed = set(native["membership_receipt"]["selected"])
        if (
            executed != {row["selector"] for row in packet["collection"]["selected"]}
            or members & executed
        ):
            raise ValueError("Native project executed membership differs from admission")
        seen.add(project)
        files.update(found)
        members.update(executed)
    return files, members


_UNITTEST_OBSERVER = r"""
import json, pathlib, sys, unittest
project, environment, report = map(pathlib.Path, sys.argv[1:])
assert pathlib.Path.cwd() == project
assert pathlib.Path(sys.prefix).resolve() == environment.resolve()
loader = unittest.TestLoader()
suite = loader.discover(str(project / "tests"))
def members(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from members(item)
        else:
            yield item.id()
selected = list(members(suite))
class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started, self.passed = [], []
    def startTest(self, test):
        self.started.append(test.id())
        super().startTest(test)
    def addSuccess(self, test):
        self.passed.append(test.id())
        super().addSuccess(test)
result = unittest.TextTestRunner(resultclass=Result).run(suite)
payload = dict(
    selected=selected, started=result.started, passed=result.passed,
    tests_run=result.testsRun, loader_errors=loader.errors,
    failures=[test.id() for test, _ in result.failures],
    errors=[test.id() for test, _ in result.errors],
    skipped=[test.id() for test, _ in result.skipped],
    expected_failures=[test.id() for test, _ in result.expectedFailures],
    unexpected_successes=[test.id() for test in result.unexpectedSuccesses],
    project=str(project), environment=str(environment), interpreter=sys.executable,
)
report.write_text(json.dumps(payload), encoding="utf-8")
raise SystemExit(0 if result.wasSuccessful() else 1)
"""


def require_unittest_project_pass(report: dict[str, Any]) -> None:
    """Require the exact native suite, with no empty or non-passing execution."""
    selected = report.get("selected")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(member, str) or not member for member in selected)
        or len(selected) != len(set(selected))
        or report.get("started") != selected
        or report.get("passed") != selected
        or type(report.get("tests_run")) is not int
        or report["tests_run"] != len(selected)
        or any(
            report.get(field) != []
            for field in (
                "loader_errors",
                "failures",
                "errors",
                "skipped",
                "expected_failures",
                "unexpected_successes",
            )
        )
    ):
        raise ValueError("Native unittest membership is empty, duplicated, skipped or non-PASS")


def check_shipped_stdlib_unittest_projects(root: Path = ROOT) -> tuple[dict[str, Any], ...]:
    """Check every captured standalone stdlib project from the original Goon census.

    This bounded proof makes no repository-consumer coverage claim. Projects
    requiring runtime/dev dependencies retain their separately selected owner.
    """
    packages = discover_template_packages(
        root / "templates/goons", source_name="goon.yml", kind="Goon"
    )
    scratch = Path(tempfile.mkdtemp(prefix="oh-goon-stdlib-project-checks-"))
    quiescent = True
    failure: BaseException | None = None
    reports: list[dict[str, Any]] = []

    def observe_process(event: CommandProcessLifecycleEvent) -> None:
        nonlocal quiescent
        if event.kind == "PROCESS_STARTING":
            quiescent = False
        elif event.kind == "DESCENDANTS_QUIESCENT":
            quiescent = True

    try:
        for key, package in packages.items():
            with (package / "goon.yml").open(encoding="utf-8", newline="") as stream:
                content = stream.read()
            compiled = parse_goon_template(
                content, source=str(package / "goon.yml"), expected_name=package.name
            )
            standalone_python = compiled.runtime == "python" and compiled.application is None
            if not standalone_python and not (package / "pyproject.toml").is_file():
                continue
            captured = capture_project_source(
                package, compiled, yaml_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest()
            )
            directory = scratch / key
            project = directory / "project"
            materialize_python_goon_project(captured.archive, captured.identity, project)
            metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
            declaration = metadata.get("project", {})
            if (
                declaration.get("dependencies") != []
                or declaration.get("optional-dependencies")
                or metadata.get("dependency-groups")
                or metadata.get("tool", {}).get("uv")
            ):
                continue
            if not standalone_python:
                raise ValueError("Stdlib project requires a captured standalone Python Goon")
            environment = directory / "environment"
            report_path = directory / "unittest.json"
            child_environment = {
                **without_test_report_environment(os.environ),
                "UV_PROJECT_ENVIRONMENT": str(environment),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            commands = (
                (
                    "uv",
                    "sync",
                    "--project",
                    str(project),
                    "--locked",
                    "--no-default-groups",
                    "--no-python-downloads",
                ),
                (
                    "uv",
                    "run",
                    "--project",
                    str(project),
                    "--offline",
                    "--frozen",
                    "--no-sync",
                    "--no-default-groups",
                    "python",
                    "-I",
                    "-c",
                    _UNITTEST_OBSERVER,
                    str(project),
                    str(environment),
                    str(report_path),
                ),
            )
            invocations = []
            for command in commands:
                try:
                    result = run_command(
                        *command,
                        cwd=project,
                        env=child_environment,
                        timeout=180,
                        process_observer=observe_process,
                        require_process_group_quiescence=True,
                        parent_death_signal=True,
                        max_capture_bytes_per_stream=64 * 1024,
                        retain_output_tail=True,
                    )
                except (CommandCancelledError, CommandTimeoutExpired) as exc:
                    quiescent = exc.supervisor.final_process_group_quiescent
                    raise
                quiescent = result.supervisor.final_process_group_quiescent
                if result.returncode or not quiescent:
                    raise ValueError(
                        "Native standalone project check failed or did not settle: " + result.stderr
                    )
                invocations.append(
                    {
                        "argv": list(result.argv),
                        "returncode": result.returncode,
                        "process_group_quiescent": quiescent,
                    }
                )
            with report_path.open("rb") as report_stream:
                encoded = report_stream.read(1024 * 1024 + 1)
            if len(encoded) > 1024 * 1024:
                raise ValueError("Native unittest report exceeds its byte bound")
            report = json.loads(encoded)
            if not isinstance(report, dict):
                raise ValueError("Native unittest report requires an object")
            require_unittest_project_pass(report)
            if (
                report.get("project") != str(project)
                or report.get("environment") != str(environment)
                or Path(report.get("interpreter", "")).parent != environment / "bin"
            ):
                raise ValueError("Native unittest executed in another project environment")
            reports.append(
                {
                    "package": key,
                    "source_identity": captured.identity.content_sha256,
                    "native_execution": invocations,
                    "native_report": report,
                }
            )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if quiescent:
            try:
                shutil.rmtree(scratch)
            except OSError:
                if failure is None:
                    raise
                failure.add_note("Native standalone project scratch cleanup failed")
        else:
            message = (
                f"Native standalone project scratch retained: process cleanup unproven: {scratch}"
            )
            if failure is None:
                raise RuntimeError(message)
            failure.add_note(message)
    return tuple(reports)


def _admit_report_directory(path: Path, root: Path) -> tuple[Path, int, tuple[int, int]]:
    if not path.is_absolute() or ".." in path.parts or path.is_relative_to(root.resolve()):
        raise ValueError("Python project reports require an absolute directory outside source")
    descriptor = _open_report_directory(path)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            raise ValueError("Python project report directory must be private")
        for name in ("summary.json", *(f"{name}.junit.xml" for name, _, _ in _PROJECTS)):
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise FileExistsError(f"Python project report already exists: {name}")
        for name in ("summary.json", *(f"{name}.junit.xml" for name, _, _ in _PROJECTS)):
            artifact = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=descriptor,
            )
            try:
                metadata = os.fstat(artifact)
                if name == "summary.json":
                    summary_identity = metadata.st_dev, metadata.st_ino
            finally:
                os.close(artifact)
        _admit_report_directory_location(path, descriptor)
        return path, descriptor, summary_identity
    except BaseException:
        os.close(descriptor)
        raise


def run_project_tests(
    root: Path = ROOT,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    report_directory: Path | None = None,
    continue_independent: bool = False,
) -> int:
    report = (
        _admit_report_directory(report_directory, root) if report_directory is not None else None
    )
    try:
        scratch = Path(tempfile.mkdtemp(prefix="oh-goon-python-project-tests-"))
    except BaseException:
        if report is not None:
            os.close(report[1])
        raise
    quiescent = True
    exit_code = 0
    failure: BaseException | None = None
    rows: list[dict[str, Any]] = [
        {
            "name": name,
            "project": relative,
            "sync_exit_code": None,
            "pytest_exit_code": None,
            "phase": "UNSTARTED",
            "process_cleanup": "VERIFIED",
            "junit_file": f"{name}.junit.xml",
        }
        for name, relative, _ in _PROJECTS
    ]
    cleanup = "INCOMPLETE"
    cleanup_error: str | None = None

    def observe_process(event: CommandProcessLifecycleEvent) -> None:
        nonlocal quiescent
        if event.kind == "PROCESS_STARTING":
            quiescent = False
        elif event.kind == "DESCENDANTS_QUIESCENT":
            quiescent = True

    def stream(_stream: str, text: str) -> None:
        print(text, end="", flush=True)

    try:
        for row, (name, relative, tests) in zip(rows, _PROJECTS, strict=True):
            project = root / relative
            environment = {
                **without_test_report_environment(os.environ),
                "UV_PROJECT_ENVIRONMENT": str(scratch / name),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            commands = (
                (
                    "uv",
                    "sync",
                    "--project",
                    str(project),
                    "--frozen",
                    "--no-default-groups",
                    "--group",
                    "dev",
                    "--no-python-downloads",
                ),
                (
                    "uv",
                    "run",
                    "--project",
                    str(project),
                    "--offline",
                    "--frozen",
                    "--no-sync",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    *(str(project / test) for test in tests),
                    *(("--maxfail=0",) if continue_independent else ()),
                    *(("-o", "xfail_strict=true") if report is not None else ()),
                    *(
                        (f"--junitxml={report[0] / row['junit_file']}",)
                        if report is not None
                        else ()
                    ),
                ),
            )
            project_failed = False
            for phase, command in zip(("sync", "pytest"), commands, strict=True):
                row["phase"] = phase.upper()
                try:
                    result = run_command(
                        *command,
                        cwd=root,
                        env=environment,
                        timeout=600,
                        cancel_requested=cancel_requested,
                        process_observer=observe_process,
                        stream_output=stream,
                        merge_stderr=True,
                        require_process_group_quiescence=True,
                        parent_death_signal=True,
                        max_capture_bytes_per_stream=64 * 1024,
                        retain_output_tail=True,
                    )
                except (CommandCancelledError, CommandTimeoutExpired) as exc:
                    quiescent = exc.supervisor.final_process_group_quiescent
                    row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                    row["phase"] = (
                        "CANCELLED"
                        if isinstance(exc, CommandCancelledError)
                        else phase.upper() + "_TIMEOUT"
                    )
                    failure = failure or exc
                    if (
                        isinstance(exc, CommandTimeoutExpired)
                        and continue_independent
                        and quiescent
                    ):
                        project_failed = True
                        break
                    raise
                except BaseException:
                    row["phase"] = "ERROR"
                    row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                    raise
                quiescent = result.supervisor.final_process_group_quiescent
                row[phase + "_exit_code"] = result.returncode
                row["process_cleanup"] = "VERIFIED" if quiescent else "INCOMPLETE"
                if result.returncode or not quiescent:
                    exit_code = exit_code or result.returncode or 1
                    row["phase"] = (
                        phase.upper() + "_FAILED" if quiescent else "PROCESS_CLEANUP_UNVERIFIED"
                    )
                    project_failed = True
                    break
                row["phase"] = "COMPLETE" if phase == "pytest" else "SYNC"
            if not quiescent or (project_failed and not continue_independent):
                break
    except (CommandCancelledError, CommandTimeoutExpired) as exc:
        quiescent = exc.supervisor.final_process_group_quiescent
        failure = exc
        raise
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if quiescent:
            try:
                shutil.rmtree(scratch)
                cleanup = "VERIFIED"
            except OSError as exc:
                cleanup_error = "ENVIRONMENT_CLEANUP_FAILED"
                print(
                    f"Python project environment cleanup failed: {scratch}: {exc}", file=sys.stderr
                )
                if failure is not None:
                    failure.add_note(f"Python project environment cleanup failed: {scratch}")
                elif exit_code == 0:
                    exit_code = 1
        else:
            cleanup_error = "PROCESS_CLEANUP_UNVERIFIED"
            print(
                f"Python project environment retained: process cleanup is unproven: {scratch}",
                file=sys.stderr,
            )
        if report is not None:
            path, descriptor, identity = report
            try:
                _admit_report_directory_location(path, descriptor)
                current = os.stat("summary.json", dir_fd=descriptor, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != identity:
                    raise RuntimeError("Python project report destination changed")
                publish_text_file(
                    path / "summary.json",
                    json.dumps(
                        {
                            "schema": PROJECT_REPORT_SCHEMA,
                            "projects": rows,
                            "cleanup": cleanup,
                            "cleanup_error": cleanup_error,
                            "first_nonzero_exit_code": exit_code or None,
                            "exception": type(failure).__name__ if failure is not None else None,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                )
                _admit_report_directory_location(path, descriptor)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Python project report publication failed: {exc}", file=sys.stderr)
                if failure is not None:
                    failure.add_note("Python project report publication failed")
                elif exit_code == 0:
                    exit_code = 1
            finally:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    print(f"Python project report handle cleanup failed: {exc}", file=sys.stderr)
                    if failure is not None:
                        failure.add_note("Python project report handle cleanup failed")
                    elif exit_code == 0:
                        exit_code = 1
    if failure is not None:
        raise failure
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-directory", type=Path)
    parser.add_argument("--continue-independent", action="store_true")
    arguments = parser.parse_args(argv)
    return run_project_tests(
        report_directory=arguments.report_directory,
        continue_independent=arguments.continue_independent,
    )


if __name__ == "__main__":
    raise SystemExit(main())
