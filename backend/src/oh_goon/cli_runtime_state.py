from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import TypedDict

from oh_goon import __version__
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import DatabaseSchemaMismatch, FleetDomainError
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.vocabulary import SystemHealthStatus, ToolStatus
from oh_goon.runtime.agent_providers import agent_providers_path
from oh_goon.runtime.database import DatabasePathIdentityLost, database_path_for_environment
from oh_goon.runtime.league_folders import league_preferences_path_for_environment

_PRESERVE_STATE_ACTION = (
    "Preserve existing state and its SQLite sidecars. Use the matching original runtime "
    "to inspect and settle retained work; doctor does not initialize, repair or recover state."
)


class _DoctorContext(TypedDict):
    implementation: dict[str, str]
    paths: dict[str, str]
    configured_roots: dict[str, str | None]


def _doctor_context() -> _DoctorContext:
    environment = dict(os.environ)
    database = database_path_for_environment(environment)
    return {
        "implementation": {
            "version": __version__,
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "platform": sys.platform,
        },
        "paths": {
            "database": str(database),
            "league": str(league_preferences_path_for_environment(environment)),
            "providers": str(agent_providers_path(environment)),
            "application_state": str(ApplicationStatePathLayout.from_database(database).root),
        },
        "configured_roots": {
            name: environment.get(name)
            for name in (
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "XDG_CACHE_HOME",
                "APPDATA",
                "LOCALAPPDATA",
            )
        },
    }


def _print_doctor_context(context: _DoctorContext) -> None:
    for name, observed in context["implementation"].items():
        print(f"{name:<17} {observed}")
    for name, path in context["paths"].items():
        print(f"{name:<17} {path}")
    for name, configured in context["configured_roots"].items():
        if configured:
            print(f"{name:<17} {configured}")


def run_basic_doctor(args: argparse.Namespace) -> int:
    from oh_goon.services.system_health import SystemHealthService

    tools = SystemHealthService.inspect_tools()
    context = _doctor_context()
    ready = all(not tool.required or tool.status is ToolStatus.READY for tool in tools)
    if args.as_json:
        print(
            json.dumps(
                {
                    "schema": "oh-goon.doctor/v1",
                    "scope": "tools",
                    "status": "ready" if ready else "setup_required",
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                    **context,
                },
                sort_keys=True,
            )
        )
    else:
        for tool in tools:
            version = f" ({tool.version})" if tool.version else ""
            print(f"{tool.name:<8} {tool.status}{version}")
        _print_doctor_context(context)
        print("Use oh-goon doctor --runtime for explicit application runtime diagnostics.")
    return 0 if ready else 2


def run_runtime_doctor(args: argparse.Namespace) -> int:
    """Inspect retained state through the original observation-only runtime."""
    from oh_goon.services.system_health import SystemHealthService

    context = _doctor_context()
    runtime: ApplicationRuntime | None = None
    failure: Exception | None = None
    cleanup_failed = False
    health = None
    try:
        runtime = ApplicationRuntime.build(startup_purpose="observation")
        health = runtime.system_health.inspect()
    except (FleetDomainError, OSError, sqlite3.Error, UnicodeError, ValueError, ImportError) as exc:
        failure = exc
    finally:
        if runtime is not None:
            try:
                runtime.shutdown(wait_for_runs=False)
            except Exception:
                cleanup_failed = True
    if failure is not None:
        code = (
            "STATE_SCHEMA_INCOMPATIBLE"
            if isinstance(failure, DatabaseSchemaMismatch)
            else (
                "STATE_IDENTITY_UNAVAILABLE"
                if isinstance(failure, DatabasePathIdentityLost)
                else "STATE_OBSERVATION_UNAVAILABLE"
            )
        )
        tools = SystemHealthService.inspect_tools()
        payload = {
            "schema": "oh-goon.doctor/v1",
            "scope": "runtime",
            "status": "blocked",
            "code": code,
            "safe_next_action": _PRESERVE_STATE_ACTION,
            "tools": [tool.model_dump(mode="json") for tool in tools],
            **context,
        }
        if cleanup_failed:
            payload["cleanup"] = "INCOMPLETE"
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for tool in tools:
                version = f" ({tool.version})" if tool.version else ""
                print(f"{tool.name:<8} {tool.status}{version}")
            _print_doctor_context(context)
            print(f"Runtime diagnosis blocked: {code}")
            print(_PRESERVE_STATE_ACTION)
            if cleanup_failed:
                print("Diagnostic runtime cleanup: INCOMPLETE")
        return 2
    assert health is not None
    if args.as_json:
        print(
            json.dumps(
                {
                    "schema": "oh-goon.doctor/v1",
                    "scope": "runtime",
                    **health.model_dump(mode="json"),
                    **context,
                    **({"cleanup": "INCOMPLETE"} if cleanup_failed else {}),
                },
                sort_keys=True,
            )
        )
    else:
        for tool in health.tools:
            version = f" ({tool.version})" if tool.version else ""
            print(f"{tool.name:<8} {tool.status}{version}")
        _print_doctor_context(context)
        proof = health.runtime_safety
        print(f"runtime  {proof.status} effects_allowed={str(proof.effects_allowed).lower()}")
        for finding in proof.findings:
            print(f"  invariant {finding.code}: {finding.message}")
        for background in health.background_failures:
            print(
                f"  background {background.code} stage={background.stage.value}: {background.message}"
            )
        if cleanup_failed:
            print("Diagnostic runtime cleanup: INCOMPLETE")
    return 0 if health.status is SystemHealthStatus.READY and not cleanup_failed else 2


def _exclude_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        record = runtime.repositories.exclude(
            str(args.repository), args.reason, args.expected_repository_instance_id
        )
    except FleetDomainError as exc:
        print(str(exc))
        return 2
    print(f"EXCLUDED  {record.repository.path}")
    print(f"Reason:    {record.exclusion_reason}")
    return 0


def _include_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        record = runtime.repositories.include(
            str(args.repository), args.expected_repository_instance_id
        )
    except FleetDomainError as exc:
        print(str(exc))
        return 2
    print(f"INCLUDED  {record.repository.path}")
    return 0


def _excluded_command(runtime: ApplicationRuntime, _args: argparse.Namespace) -> int:
    records = runtime.repositories.exclusions()
    if not records:
        print("No repositories are excluded.")
        return 0
    print("Excluded repositories")
    for record in records:
        print(f"- {record['path']}  [{record['reason']}]")
    return 0


def run_state_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    handlers = {
        "exclude": _exclude_command,
        "include": _include_command,
        "excluded": _excluded_command,
    }
    handler = handlers.get(args.command)
    return handler(runtime, args) if handler is not None else None
