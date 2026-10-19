from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

from oh_goon.api.local_access import ensure_local_access_token
from oh_goon.application import ApplicationRuntime
from oh_goon.cli_developer import run_state_free_developer_command
from oh_goon.cli_enforcer import run_enforcer_compare_command
from oh_goon.cli_local_state import check_local_state_for_startup, run_local_state_command
from oh_goon.cli_logs import run_logs_command
from oh_goon.cli_open import run_open_command
from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime import run_stateful_command
from oh_goon.cli_self import run_self_command
from oh_goon.cli_start import run_start_command
from oh_goon.cli_status import run_status_command
from oh_goon.cli_stop import run_stop_command
from oh_goon.cli_storage import run_storage_command
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import DatabaseSchemaMismatch
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


def _first_command_result(args, handlers) -> int | None:
    for handler in handlers:
        result = handler(args)
        if result is not None:
            return result
    return None


def _run_pre_runtime_command(args) -> int | None:
    result = _first_command_result(
        args,
        (
            run_local_state_command,
            run_storage_command,
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
    return _first_command_result(
        args,
        (run_self_command, run_enforcer_compare_command, run_state_free_developer_command),
    )


def _run_serve_command(args) -> int:
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
) -> ApplicationRuntime | None:
    try:
        if fleet_provisioner is not None:
            return ApplicationRuntime.build(fleet_provisioner=fleet_provisioner)
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


def _execute_stateful_command(runtime: ApplicationRuntime, args) -> int:
    try:
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
    if fleet_provisioner is not None:
        from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

        if type(fleet_provisioner) is not FleetPreworkProvisioner:
            raise TrustProtocolError("original deployment FleetPreworkProvisioner required")
        if args.command != "plan" or args.plan_command != "run":
            raise TrustProtocolError("Fleet deployment handoff is only valid for plan run")
        # Cheap original-owner enrollment fence, before Plan reads, runtime
        # construction, storage/lease mutation, or any pre-runtime commands.
        fleet_provisioner.require_enrolled_identity(args.fleet_run_id)

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

    if args.command == "agent" and args.agent_command == "providers":
        from oh_goon.cli_agent_providers import run_provider_command

        return run_provider_command(args)

    # Pure authoring commands share the original FleetPlanResolver and do not
    # need a database, journal, executor, or physical native admission.  Runtime
    # submission still passes through the one FleetPlanService owner below.
    if args.command == "plan":
        if getattr(args, "plan_command", None) == "inputs":
            from oh_goon.cli_plan_definition_commands import _inputs_pre_runtime

            return _inputs_pre_runtime(args)
        if getattr(args, "plan_command", None) == "validate":
            from oh_goon.cli_plan_definition_commands import _validate_pre_runtime

            return _validate_pre_runtime(args)
        if getattr(args, "plan_command", None) == "explain":
            from oh_goon.cli_plan_definition_commands import _explain_pre_runtime

            return _explain_pre_runtime(args)
        if getattr(args, "plan_command", None) == "diff":
            from oh_goon.cli_plan_definition_commands import _diff_pre_runtime

            return _diff_pre_runtime(args)
        if getattr(args, "plan_command", None) == "check":
            from oh_goon.cli_plan_definition_commands import _check_pre_runtime

            return _check_pre_runtime(args)
        if getattr(args, "plan_command", None) == "preview":
            from oh_goon.cli_plan_definition_commands import _preview_pre_runtime

            return _preview_pre_runtime(args)

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

    runtime = _build_application_runtime(
        fleet_provisioner=fleet_provisioner, standard_local=standard_local
    )
    if runtime is None:
        return 2
    result = _execute_stateful_command(runtime, args)
    if result >= 0:
        return result
    parser.print_help()
    return 0
