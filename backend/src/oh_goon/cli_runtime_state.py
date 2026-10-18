from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.vocabulary import SystemHealthStatus


def _doctor_command(runtime: ApplicationRuntime, _args: argparse.Namespace) -> int:
    health = runtime.system_health.inspect()
    for tool in health.tools:
        version = f" ({tool.version})" if tool.version else ""
        print(f"{tool.name:<8} {tool.status}{version}")
    proof = health.runtime_safety
    print(f"runtime  {proof.status} effects_allowed={str(proof.effects_allowed).lower()}")
    for finding in proof.findings:
        print(f"  invariant {finding.code}: {finding.message}")
    for failure in health.background_failures:
        print(f"  background {failure.code} stage={failure.stage.value}: {failure.message}")
    return 0 if health.status is SystemHealthStatus.READY else 2


def _exclude_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        record = runtime.repositories.exclude(str(args.repository), args.reason)
    except FleetDomainError as exc:
        print(str(exc))
        return 2
    print(f"EXCLUDED  {record.repository.path}")
    print(f"Reason:    {record.exclusion_reason}")
    return 0


def _include_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        record = runtime.repositories.include(str(args.repository))
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
        "doctor": _doctor_command,
        "exclude": _exclude_command,
        "include": _include_command,
        "excluded": _excluded_command,
    }
    handler = handlers.get(args.command)
    return handler(runtime, args) if handler is not None else None
