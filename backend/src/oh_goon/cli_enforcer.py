from __future__ import annotations

import argparse
import difflib
from pathlib import Path

import yaml
from pydantic import ValidationError

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.enforcer_receipt import EnforcerRunReceipt
from oh_goon.services.enforcer_run_receipt import render_enforcer_yaml


def run_enforcer_compare_command(args: argparse.Namespace) -> int | None:
    """Keep receipt comparison state-free so a stale local DB never blocks debugging."""
    if args.command != "enforcer" or args.enforcer_command != "compare":
        return None
    return _compare_enforcer_receipts(args.before, args.after)


def run_enforcer_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    """Inspect one Team through the same goon.yml admission contract used by execution."""
    if args.command != "enforcer":
        return None
    if args.enforcer_command != "run":
        return 2
    try:
        inspection = runtime.goon.inspect(args.repository, inputs=plan_inputs(args))
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2

    team = inspection.team
    preview = inspection.preview
    if args.as_json:
        print(
            inspection.preview.model_dump_json(
                indent=2,
            )
        )
        return 0

    print(f"Team: {team.repository.path}")
    print("Entrypoint: goon.yml")
    print(f"Plan ID: {preview.plan_id}")
    print("Enforcer: valid")
    print(f"Resolved steps: {len(preview.steps)}")
    print("Nothing executed.")
    return 0


def _compare_enforcer_receipts(before: Path, after: Path) -> int:
    try:
        before_receipt = _load_enforcer_receipt(before)
        after_receipt = _load_enforcer_receipt(after)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        print(f"Enforcer compare failed: {exc}")
        return 2

    before_text = render_enforcer_yaml(before_receipt).splitlines(keepends=True)
    after_text = render_enforcer_yaml(after_receipt).splitlines(keepends=True)
    if before_text == after_text:
        print("Enforcer receipts are identical.")
        return 0
    print(
        "".join(
            difflib.unified_diff(
                before_text,
                after_text,
                fromfile=str(before),
                tofile=str(after),
            )
        ),
        end="",
    )
    return 1


def _load_enforcer_receipt(path: Path) -> EnforcerRunReceipt:
    resolved = path.expanduser().resolve()
    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Enforcer receipt must be a YAML mapping: {resolved}")
    return EnforcerRunReceipt.model_validate(payload)
