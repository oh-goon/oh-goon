from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend
    from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_enforcer import run_enforcer_compare_command
from oh_goon.cli_local_state import check_local_state_for_startup, run_local_state_command
from oh_goon.cli_logs import run_logs_command
from oh_goon.cli_open import run_open_command
from oh_goon.cli_parser import build_parser
from oh_goon.cli_start import run_start_command
from oh_goon.cli_status import run_status_command
from oh_goon.cli_stop import run_stop_command
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import DatabaseSchemaMismatch, FleetDomainError
from oh_goon.runtime.database import DatabasePathIdentityLost
from oh_goon.runtime.storage_health import storage_unavailable_reason


def _print_runtime_database_identity_lost(reason: str) -> None:
    print("OH-GOON RUNTIME DATABASE IDENTITY LOST")
    print(f"Reason: {reason}")
    print(
        "The admitted SQLite identity could not be re-proven after bounded observation-only checks."
    )
    print(
        "Oh-Goon will not recreate/repair the database or replay a Plan, Step, or external effect."
    )
    print(
        "For isolated certification state, preserve this diagnostic before using "
        "make certification-state-reset; then rerun make certify."
    )
    print(
        "If the failure repeats after reset, treat it as a source/storage defect, "
        "not as disposable-cache damage."
    )
    print("Native package-manager state is outside certification control-state reset authority.")


def _print_runtime_storage_unavailable(reason: str) -> None:
    print("OH-GOON RUNTIME STORAGE UNAVAILABLE")
    print(f"Reason: {reason}")
    print("Execution evidence/state storage became unavailable after runtime startup.")
    print("This is an environment/storage failure, not a source or test result.")
    print("Restore writable host storage, then rerun the owning command.")


def _shutdown_after_primary_failure(runtime: ApplicationRuntime) -> None:
    """Never let secondary shutdown storage loss mask the command's primary failure."""

    try:
        runtime.shutdown(wait_for_runs=False)
    except Exception as exc:
        reason = storage_unavailable_reason(exc)
        if reason is not None:
            return
        # A primary exception is already in flight. Shutdown is cleanup and must not
        # replace that primary semantic failure with a secondary cleanup traceback.
        return


def _shutdown_after_completed_command(
    runtime: ApplicationRuntime,
) -> tuple[str, str] | None:
    try:
        runtime.shutdown(wait_for_runs=False)
    except DatabasePathIdentityLost as exc:
        return ("database_identity", str(exc))
    except Exception as exc:
        reason = storage_unavailable_reason(exc)
        if reason is not None:
            return ("storage", reason)
        raise
    return None


def _first_command_result(
    args: argparse.Namespace, handlers: Sequence[Callable[[argparse.Namespace], int | None]]
) -> int | None:
    for handler in handlers:
        result = handler(args)
        if result is not None:
            return result
    return None


def _run_pre_runtime_command(args: argparse.Namespace) -> int | None:
    if args.command == "storage":
        from oh_goon.cli_storage import run_storage_command

        return run_storage_command(args)
    result = _first_command_result(
        args,
        (
            run_local_state_command,
            run_status_command,
            run_open_command,
            run_logs_command,
            run_stop_command,
            run_start_command,
        ),
    )
    if result is not None:
        return result
    if args.command == "serve":
        return _run_serve_command(args)
    if args.command in {"self", "dev"}:
        from oh_goon.cli_self import run_self_command

        result = run_self_command(args)
        if result is not None:
            return result
    result = run_enforcer_compare_command(args)
    if result is not None:
        return result
    if args.command == "dev":
        from oh_goon.cli_developer import run_state_free_developer_command

        return run_state_free_developer_command(args)
    return None


def _run_serve_command(args: argparse.Namespace) -> int:
    from oh_goon.api.local_access import ensure_local_access_token

    if check_local_state_for_startup() != 0:
        return 2
    token = ensure_local_access_token()
    print("oh-goon local API")
    print(f"Local access token: {token}")
    print("Send it as X-Oh-Goon-Local-Access on API requests.")
    import uvicorn

    uvicorn.run("oh_goon.api.app:app", host=args.host, port=args.port, reload=False)
    return 0


def _build_application_runtime(
    *,
    fleet_provisioner: FleetPreworkProvisioner | None = None,
    standard_local: bool | None = None,
    standard_process_isolation: ProcessIsolationBackend | None = None,
    git_service: GitService | None = None,
    run_resource_scope: ResolvedRunResourceScope | None = None,
    run_resource_envelope: RunResourceEnvelopeEvidence | None = None,
) -> ApplicationRuntime | None:
    try:
        if run_resource_scope is not None or run_resource_envelope is not None:
            return ApplicationRuntime.build(
                standard_local=standard_local,
                run_resource_scope=run_resource_scope,
                run_resource_envelope=run_resource_envelope,
            )
        if fleet_provisioner is not None:
            return ApplicationRuntime.build(fleet_provisioner=fleet_provisioner)
        if git_service is not None:
            return ApplicationRuntime.build(
                standard_local=standard_local,
                standard_process_isolation=standard_process_isolation,
                git_service=git_service,
            )
        return ApplicationRuntime.build(standard_local=standard_local)
    except DatabaseSchemaMismatch as exc:
        print(str(exc))
    except DatabasePathIdentityLost as exc:
        _print_runtime_database_identity_lost(str(exc))
    except Exception as exc:
        reason = storage_unavailable_reason(exc)
        if reason is None:
            raise
        _print_runtime_storage_unavailable(reason)
    return None


def _execute_stateful_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        from oh_goon.cli_runtime import run_stateful_command

        result = run_stateful_command(runtime, args)
    except BaseException as exc:
        _shutdown_after_primary_failure(runtime)
        if isinstance(exc, DatabasePathIdentityLost):
            _print_runtime_database_identity_lost(str(exc))
            return 2
        reason = storage_unavailable_reason(exc)
        if reason is None:
            raise
        _print_runtime_storage_unavailable(reason)
        return 2

    shutdown_failure = _shutdown_after_completed_command(runtime)
    if shutdown_failure is not None:
        kind, reason = shutdown_failure
        if kind == "database_identity":
            _print_runtime_database_identity_lost(reason)
        else:
            _print_runtime_storage_unavailable(reason)
        return result if result != 0 else 2
    return result


def main(
    argv: list[str] | None = None,
    *,
    fleet_provisioner: FleetPreworkProvisioner | None = None,
) -> int:
    """Run the CLI; only an in-process deployment can transport Fleet authority.

    The shell executable passes no provisioner. This keyword accepts only the
    original deployment-owned object, not a Run/Plan parameter, file, environment
    name or serialized credential. Membership is a pre-runtime ingress check,
    *not* admission; the original lifecycle still resolves and reobserves it.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    resource_handoff = None
    resource_evidence = None
    if fleet_provisioner is not None:
        from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

        if type(fleet_provisioner) is not FleetPreworkProvisioner:
            raise TrustProtocolError("original deployment FleetPreworkProvisioner required")
        if args.command != "run" and not (
            args.command == "dev" and args.dev_command == "_run-plan"
        ):
            raise TrustProtocolError(
                "Fleet deployment handoff is only valid for a selected run or internal certification run"
            )
        # Enrollment precedes all config/source reads and runtime mutation.
        fleet_provisioner.require_enrolled_identity(args.fleet_run_id)

    if args.command == "dev" and args.dev_command in {"_lock-plan", "_run-plan"}:
        from oh_goon.core.errors import FleetPlanInvalid
        from oh_goon.core.template_identity import TemplateClassification, template_classification
        from oh_goon.runtime.plan_templates import PlanTemplateStore
        from oh_goon.runtime.template_discovery import template_package

        store = PlanTemplateStore()
        selected = args.plan.resolve()
        relative = selected.relative_to(store.root) if selected.is_relative_to(store.root) else None
        if relative is None or len(relative.parts) != 3 or relative.name != "plan.yml":
            raise FleetPlanInvalid(
                "Internal certification ingress requires its exact shipped internal Plan"
            )
        key = "/".join(relative.parts[:2])
        if (
            template_classification(key, kind="Plan")
            is not TemplateClassification.INTERNAL_SELF_HOSTING
        ):
            raise FleetPlanInvalid("Internal certification ingress requires an internal Plan")
        _, directory = template_package(store.root, key, source_name="plan.yml", kind="Plan")
        if selected != directory / "plan.yml":
            raise FleetPlanInvalid("Internal certification ingress requires its canonical source")
        if args.dev_command == "_lock-plan":
            args.command = "lock"
        else:
            args.command, args.plan_command = "plan", "run"

    if args.command == "config":
        from oh_goon.cli_configuration import run_config_command

        return run_config_command(args)

    if hasattr(args, "configuration"):
        from oh_goon.cli_configuration import prepare_configuration
        from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
        from oh_goon.services.run_configuration import load_run_configuration

        try:
            if getattr(args, "_resource_handoff", None) is not None:
                if fleet_provisioner is not None:
                    raise FleetDomainError(
                        "Deployment execution cannot restore a local resource handoff"
                    )
                from oh_goon.cli_run_resources import verify_run_resource_handoff

                resource_handoff, resource_evidence = verify_run_resource_handoff(
                    args._resource_handoff, args.configuration
                )
                resource_handoff.state.restore()
                snapshot = resource_handoff.snapshot
                args.fleet_run_id = resource_handoff.scope.fleet_run_id
            else:
                snapshot = load_run_configuration(args.configuration)
            if snapshot.selection.resources is not None:
                if args.command == "run" and resource_handoff is None:
                    if fleet_provisioner is not None:
                        raise FleetDomainError(
                            "Deployment execution cannot select local Run resources"
                        )
                    from oh_goon.cli_run_resources import run_with_resource_envelope

                    return run_with_resource_envelope(
                        args, snapshot, list(argv) if argv is not None else sys.argv[1:]
                    )
                if args.command not in {"run", "lock"}:
                    raise FleetDomainError(
                        "Whole-Run resources require the ordinary waited run command"
                    )
            prepare_configuration(args, snapshot=snapshot)
        except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
            print_safe_plan_diagnostic(exc, as_json=bool(getattr(args, "as_json", False)))
            return 2
        if args.command == "run":
            args.command, args.plan_command = "plan", "run"
    standard_local = None
    if args.command == "plan" and args.plan_command == "run":
        from oh_goon._application_foundation import standard_local_profile

        standard_local = standard_local_profile(
            args.standard_local, deployment_selected=fleet_provisioner is not None
        )
        args.standard_local = standard_local
        if standard_local and (fleet_provisioner is not None or not args.wait):
            parser.error(
                "local execution waits for completion and cannot use deployment enrollment"
            )

    if args.command == "templates":
        from oh_goon.cli_templates import run_template_command

        return run_template_command(args)

    if args.command == "ai":
        from oh_goon.cli_ai import run_ai_command

        return run_ai_command(args)

    if args.command == "agent" and args.agent_command == "providers":
        from oh_goon.cli_agent_providers import run_provider_command

        return run_provider_command(args)

    if args.command == "agent" and args.agent_command == "service":
        from oh_goon.cli_agent_services import run_service_command

        return run_service_command(args)

    if args.command == "plan":
        if args.plan_command == "diff":
            from oh_goon.cli_plan_definition_commands import _diff_pre_runtime

            return _diff_pre_runtime(args)
        if args.plan_command == "check":
            from oh_goon.cli_plan_definition_commands import _check_pre_runtime

            return _check_pre_runtime(args)

    # `oh-goon lock` writes only its explicit goon.lock through the original
    # FleetPlanService lock writer. It must not construct the app/Fleet database.
    if args.command == "agent":
        from oh_goon.cli_agent_patch_apply import run_selected_proposal

        return run_selected_proposal(args)

    if args.command == "lock":
        from oh_goon.cli_lock import run_stateless_lock

        return run_stateless_lock(args)

    direct_result = _run_pre_runtime_command(args)
    if direct_result is not None:
        return direct_result

    if args.command == "plan" and args.plan_command == "run" and args.native_agent is not None:
        if not standard_local or not args.wait:
            parser.error("native Agent execution requires ordinary waited execution")
        try:
            from oh_goon.cli_ai import native_plan_selection

            selected, isolation, git = native_plan_selection(args)
        except (FleetDomainError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        args._native_agent = selected
        runtime = _build_application_runtime(
            standard_local=standard_local, standard_process_isolation=isolation, git_service=git
        )
    else:
        if resource_handoff is not None:
            runtime = _build_application_runtime(
                standard_local=standard_local,
                run_resource_scope=resource_handoff.scope,
                run_resource_envelope=resource_evidence,
            )
        else:
            runtime = _build_application_runtime(
                fleet_provisioner=fleet_provisioner, standard_local=standard_local
            )
    if runtime is None:
        return 2
    result = _execute_stateful_command(runtime, args)
    if resource_handoff is not None and resource_evidence is not None:
        from oh_goon.cli_run_resources import publish_terminal_run_resource_context

        try:
            publish_terminal_run_resource_context(runtime, resource_handoff, resource_evidence)
        except Exception:
            print("RUN_RESOURCE_OBSERVATION_CONTEXT_INCOMPLETE", file=sys.stderr)
            return result if result != 0 else 2
    if result >= 0:
        return result
    parser.print_help()
    return 0
