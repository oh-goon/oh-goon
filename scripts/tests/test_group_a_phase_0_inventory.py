"""Source census guards; no runtime admission or release qualification claimed.

When a new mutation ingress or semantic call edge appears, fail early so its
migration intent, negative regressions, and documentation get reviewed.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "backend/src/oh_goon/api/routes"
ROOT_SRC = ROOT / "backend/src/oh_goon"

# Mutation routes in the *inspected* execution/placement/recovery/League modules.
# Do not treat a POST read-only preview as a workload start.
EXPECTED_MUTATION_ROUTES = {
    "fleet_runs": frozenset(
        {"start_fleet_run", "readmit_fleet_run", "rerun_fleet_run", "cancel_fleet_run"}
    ),
    "fleet_executors": frozenset(
        {
            "cancel_queued_admission",
            "reprioritize_queued_admission",
            "drain_executor",
            "activate_executor",
            "revoke_executor_trust",
            "restore_executor_trust",
        }
    ),
    "schedules": frozenset({"create_from_shared_plan", "replace", "delete", "start"}),
    "recovery": frozenset({"reconcile_effect", "acknowledge_effect"}),
    "repositories": frozenset({"exclude_repository", "include_repository"}),
    "league": frozenset(
        {
            "run_team",
            "create_shared_plan_from_template",
            "save_shared_plan",
            "preview_shared_plan_lock",
            "preview_shared_plan",
            "run_shared_plan",
            "save_lineup",
        }
    ),
}


READ_ONLY_POST_ROUTES = frozenset({"inspect_shared_plan_inputs"})


def _mutating_routes(source: str) -> frozenset[str]:
    module = ast.parse(source)
    names = set()
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if (
                isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
                and decorator.func.attr in {"post", "put", "patch", "delete"}
            ):
                names.add(node.name)
    return frozenset(names - READ_ONLY_POST_ROUTES)


def _calls(file: str, symbol: str) -> frozenset[str]:
    tree = ast.parse((ROOT_SRC / file).read_text(encoding="utf-8"))
    parts = symbol.split(".")
    body = tree.body
    for part in parts:
        member = next(
            (
                x
                for x in body
                if isinstance(x, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and x.name == part
            ),
            None,
        )
        assert member is not None, f"Missing owner: {file}::{symbol}"
        body = member.body
    return frozenset(
        ast.unparse(x.func)
        for node in body
        for x in ast.walk(node)
        if isinstance(x, ast.Call)
    )


def test_execution_mutation_route_census_fails_on_new_or_removed_ingress() -> None:
    for module, expected in EXPECTED_MUTATION_ROUTES.items():
        actual = _mutating_routes((ROUTES / f"{module}.py").read_text(encoding="utf-8"))
        assert actual == expected, (
            f"{module}: added/removed/renamed route needs authority inventory review"
        )


def test_exempt_post_is_only_read_only_input_inspection() -> None:
    source = (ROUTES / "league.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "inspect_shared_plan_inputs"
    )
    calls = {
        ast.unparse(node.func)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }
    assert "service.inspect_inputs" in calls
    assert not calls.intersection(
        {"service.run", "service.save", "service.start", "service.delete"}
    )
    assert 'operation_id="inspect_shared_plan_inputs"' in source
    assert '"/shared-plan-input-inspections"' in source
    docstring = ast.get_docstring(function)
    assert docstring is not None and "not admission" in docstring


def test_route_census_detects_uninventoried_mutation_in_synthetic_source() -> None:
    source = "@router.post('/exec')\ndef launch():\n    pass\n"
    assert _mutating_routes(source) == {"launch"}
    assert _mutating_routes(source) != EXPECTED_MUTATION_ROUTES["fleet_runs"]


def test_canonical_call_edges_and_current_migration_seams_still_exist() -> None:
    edges = (
        (
            "services/fleet_plans.py",
            "FleetPlanService.run",
            {"self.prepare_execution_target", "self._runs.start_prepared_plan"},
        ),
        (
            "services/fleet_plans.py",
            "FleetPlanService.run_reviewed",
            {
                "self.prepare_reviewed_execution_target",
                "self._runs.start_prepared_plan",
            },
        ),
        (
            "services/fleet_run_control_plane.py",
            "FleetRunControlPlane._start_intent",
            {"self._admission.plan", "self._lifecycle.admit"},
        ),
        (
            "services/fleet_run_lifecycle.py",
            "FleetRunLifecycle.admit",
            {"self._executor_pool.admit_strict_with"},
        ),
        (
            "services/direct_effect_admission.py",
            "DirectEffectAdmission.run",
            {"self._effects.execute_direct_claimed"},
        ),
        (
            "services/schedule_execution.py",
            "ScheduleExecutionEngine._start_repository",
            {"self._runs.start_bound"},
        ),
        (
            "runtime/_executor_placement.py",
            "ExecutorPlacementAdmission.admit_with",
            {"_placement_order", "self._try_once"},
        ),
        (
            "integrations/fleet_plans.py",
            "FleetPlanRunner.prepare",
            {"self._steps.prepare_direct_run", "self._steps.prepare_strict_run"},
        ),
        (
            "integrations/fleet_plan_run_execution.py",
            "FleetPlanRunExecutor.prepare_strict_run",
            {"select_bound_process_isolation_backend"},
        ),
        (
            "services/recovery.py",
            "RecoveryService.reconcile",
            {"self._recovery.reconcile"},
        ),
        (
            "services/recovery.py",
            "RecoveryService.acknowledge",
            {"self._recovery.acknowledge"},
        ),
        ("cli_runtime_plans.py", "_run", {"runtime.plans.run"}),
        (
            "cli_runtime_operations.py",
            "run_operation_command",
            {"runtime.operations.run"},
        ),
        (
            "cli_runtime_recovery.py",
            "run_recovery_command",
            {"runtime.recovery.reconcile", "runtime.recovery.acknowledge"},
        ),
    )
    for file, owner, required in edges:
        observed = _calls(file, owner)
        assert required <= observed, (
            f"{file}::{owner} changed — re-review the one-owner cut: {required - observed}"
        )


def test_legacy_backend_selector_and_startup_recovery_are_inventory_not_proof_of_lace() -> (
    None
):
    configuration = _calls(
        "runtime/_executor_configuration.py", "_parse_executor_backend"
    )
    assert "_bubblewrap_backend" in configuration
    assert "_opensandbox_settings" in configuration
    isolation = _calls(
        "integrations/fleet_plan_process_isolation.py",
        "select_bound_process_isolation_backend",
    )
    assert "isolation_backend_evidence" in isolation
    source = (ROOT_SRC / "_application_foundation.py").read_text(encoding="utf-8")
    assert "effect_recovery.recover()" in source
    assert "DirectEffectAdmission(" in source
