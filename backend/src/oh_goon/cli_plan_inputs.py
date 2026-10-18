from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon.cli_plan_input_files import read_caller_input_file
from oh_goon.core.errors import FleetPlanInputRejected
from oh_goon.core.models.fleet_plan_composition import PlanInputValue


def plan_inputs(args: argparse.Namespace) -> dict[str, PlanInputValue]:
    """One caller collector; typing/unknown names remain owned by canonical binder."""
    values: dict[str, PlanInputValue] = {}
    for item in getattr(args, "plan_inputs", None) or ():
        if "=" not in item:
            raise ValueError(f"Fleet plan --input must use NAME=VALUE: {item}")
        name, value = item.split("=", 1)
        if not name or name in values:
            raise ValueError(f"Fleet plan input must have one unique non-empty name: {item}")
        values[name] = value
    path = getattr(args, "inputs_file", None)
    if path is not None:
        snapshot = getattr(args, "_caller_input_file_snapshot", None)
        if snapshot is None:
            snapshot = read_caller_input_file(Path(path), root=Path.cwd())
            # This belongs to exactly this invocation's Namespace. Repeated
            # diagnostic dispatches reuse the same frozen caller observation.
            args._caller_input_file_snapshot = snapshot
        for name, value in snapshot.entries:
            if name in values:
                raise FleetPlanInputRejected(
                    f"Fleet plan caller input {name!r} is supplied by both --input and --inputs-file; remove one",
                    diagnostic_code="CONFLICTING_INPUT_SOURCES",
                    input_names=(name,),
                )
            values[name] = value
    return values
