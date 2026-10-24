"""Transport the one captured run.yml caller mapping to existing Plan owners."""

from __future__ import annotations

import argparse

from oh_goon.core.models.fleet_plan_composition import PlanInputValue


def plan_inputs(args: argparse.Namespace) -> dict[str, PlanInputValue]:
    """Never discover, parse or overlay another caller configuration route."""
    return dict(getattr(args, "_selected_inputs", {}))
