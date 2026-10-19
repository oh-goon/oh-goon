"""M149 prevent silent admission-owner regression or premature live journal activation."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def parsed(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def test_direct_and_fleet_common_cut_route_through_fleet_planner() -> None:
    direct = parsed("backend/src/oh_goon/services/direct_effect_admission.py")
    fleet = parsed("backend/src/oh_goon/services/fleet_admission.py")
    direct_run = next(
        n
        for n in ast.walk(direct)
        if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    names = {n.attr for n in ast.walk(direct_run) if isinstance(n, ast.Attribute)}
    assert "admit_repository_effect_cut" in names
    assert "admit_effect_cut" not in names
    fleet_plan = next(
        n
        for n in ast.walk(fleet)
        if isinstance(n, ast.FunctionDef) and n.name == "plan"
    )
    plan_calls = {n.attr for n in ast.walk(fleet_plan) if isinstance(n, ast.Attribute)}
    assert {"admit_runtime_cut", "require_effects_for_cut"} <= plan_calls


def test_no_live_causal_journal_activated_outside_phase4_boundary() -> None:
    for path in (
        "backend/src/oh_goon/_application_foundation.py",
        "backend/src/oh_goon/_application_services.py",
    ):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "CausalRunJournal" not in text
        assert "publish_admission" not in text
