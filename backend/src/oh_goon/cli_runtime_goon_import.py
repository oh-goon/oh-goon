from __future__ import annotations

import argparse
import os

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError


def import_gitlab(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """One-way GitLab CI/component migration into one Atomic repository Goon."""
    try:
        import_variables = _named_values(
            getattr(args, "gitlab_variables", None), label="--variable"
        )
        token_env = getattr(args, "token_env", None)
        token = os.environ.get(token_env) if token_env else None
        if token_env and token is None:
            raise ValueError(f"GitLab token environment variable is not set: {token_env}")
        migration = runtime.goon.import_gitlab(
            args.source,
            output=args.output,
            import_variables=import_variables,
            token=token,
            expected_revision=getattr(args, "expected_revision", None),
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2

    print("GitLab -> Goon one-way import")
    print(f"Source: {migration.source}")
    print(f"Components resolved: {len(migration.components)}")
    print(f"Goon steps generated: {len(migration.goon.steps)}")
    if migration.issues:
        print(f"BLOCKING ISSUES: {len(migration.issues)}")
        for issue in migration.issues:
            location = ".".join(item for item in (issue.job, issue.field) if item) or "pipeline"
            print(f"  {location}: {issue.reason}")
    if not migration.ready:
        print("Result: BLOCKED; goon.yml was not written because exact semantics are unresolved")
        return 2
    print(f"Result: READY; wrote native Atomic Goon: {args.output}")
    return 0


def _named_values(items, *, label: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items or ():
        if "=" not in item:
            raise ValueError(f"{label} must use NAME=VALUE: {item}")
        name, value = item.split("=", 1)
        if not name or name in values:
            raise ValueError(f"{label} requires unique non-empty names: {item}")
        values[name] = value
    return values
