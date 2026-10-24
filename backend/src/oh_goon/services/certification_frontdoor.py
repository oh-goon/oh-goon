from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.source_layout_policy import assert_canonical_source_layout
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_DELETE, SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.strict_executor_authority import (
    CERTIFICATION_CGROUP_ROOT_ENV,
    EXECUTORS_FILE_ENV,
    strip_strict_executor_authority,
)
from oh_goon.engine.command import run_command
from oh_goon.services.certification_bootstrap import (
    SYSTEMD_DELEGATED_ENV,
    SYSTEMD_SUBGROUP_ENV,
    CertificationEnvironmentError,
    CertificationStateBusy,
    certification_executor_unavailable,
    certification_state_lease,
    certification_state_root,
    preflight_certification_database_under_lease,
    reconcile_certification_run_workspaces,
    resolve_certification_cgroup_root,
)
from oh_goon.services.goon_static_admission import compile_canonical_goons
from oh_goon.workflow import APP_PLAN, workflow_intent

_DELEGATE_SUBGROUP = "oh-goon-workflow"
_REENTRY_FLAG = "--oh-goon-delegated-reentry"
_SYSTEMD_READY = "delegated-ready"


def main(argv: list[str] | None = None) -> int:
    """Run one canonical workflow after static source admission.

    APP/self verification consumes the already-initialized checkout directly and does
    not require strict-executor placement that it never uses. Release execution keeps
    the strict executor boundary because the release Plan can create CI/CD workload
    attempts.  The internal delegated re-entry has already passed static admission in
    its parent process, so it must not re-decide the same source authority.
    """

    args = list(argv if argv is not None else sys.argv[1:])
    if args[:1] == [_REENTRY_FLAG]:
        return _run_delegated_reentry(args[1:])

    # Reject unsupported public workflow intent before static source admission,
    # certification SQLite preflight, workspace cleanup or delegation.
    if workflow_intent(args) is None:
        print("CERTIFICATION WORKFLOW INGRESS REJECTED")
        print("usage: oh-goon-workflow [app|verify|release] | explain <fleet-run-id>")
        print("No certification state or Fleet Run was mutated.")
        return 2

    requires_strict_executor = _requires_strict_executor(args)
    if not requires_strict_executor:
        # APP/self verification must be authority-clean before even static admission.
        # Static admission is source-only today; stripping first prevents future code
        # from accidentally observing or depending on unrelated strict placement state.
        strip_strict_executor_authority(os.environ)
    if not _static_source_admission():
        return 2

    environment = _certification_process_environment(
        os.environ, require_strict_executor=requires_strict_executor
    )
    os.environ.update(environment)
    if not requires_strict_executor:
        return _workflow_main_with_state_lease(args)
    if environment.get(EXECUTORS_FILE_ENV, "").strip():
        return _workflow_main_with_state_lease(args)
    if environment.get(CERTIFICATION_CGROUP_ROOT_ENV, "").strip():
        return _workflow_main_with_state_lease(args)

    try:
        resolve_certification_cgroup_root(environment)
    except FleetPlanInvalid as current_error:
        return _enter_systemd_delegation(args, current_reason=str(current_error))
    return _workflow_main_with_state_lease(args)


def _require_deployment_source_root() -> None:
    """Require one source root for in-process APP admission, bootstrap and evidence.

    Workflow pins ROOT at module import; certification source admission and the
    state lease use the current directory. An embedded deployment may change
    cwd without changing the imported workflow authority. Never mix those.
    """
    from oh_goon import workflow
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

    if Path.cwd().resolve() != workflow.ROOT:
        raise TrustProtocolError("deployment source root differs from imported workflow authority")


def _require_deployment_process_owner(
    *, fleet_provisioner: object, fleet_run_id: str
) -> FleetPreworkProvisioner:
    """Single fail-before-work context check; custody remains with the original owner.

    Do not discover, construct, serialize or substitute a FleetPreworkProvisioner.
    This validates the process selection only; it never grants Run admission.
    """
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

    if type(fleet_provisioner) is not FleetPreworkProvisioner:
        raise TrustProtocolError("original deployment FleetPreworkProvisioner required")
    assert isinstance(fleet_provisioner, FleetPreworkProvisioner)
    fleet_provisioner.require_enrolled_identity(fleet_run_id)
    _require_deployment_source_root()
    if os.environ.get(EXECUTORS_FILE_ENV) != str(fleet_provisioner.executor_inventory_file):
        raise TrustProtocolError("deployment executor inventory differs from original Fleet owner")
    if os.environ.get(SQLITE_JOURNAL_MODE_ENV) != SQLITE_JOURNAL_DELETE:
        raise TrustProtocolError(
            "deployment must select canonical certification SQLite journal mode"
        )
    return fleet_provisioner


def _require_deployment_control_state() -> None:
    """Reject a mismatched deployment DB/League *before* any certification effects.

    The existing certification-state-root owner supplies the path computation.
    This does not create, inspect or substitute state, and is not an admission
    receipt. The later canonical bootstrap and context guard reobserve the same
    selection before publishing the APP lock or starting a Fleet Run.
    """
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    state = certification_state_root(source_root=Path.cwd(), state_home=state_home)
    for name, expected in (
        ("OH_GOON_DB", state / "oh-goon.db"),
        ("OH_GOON_LEAGUE_FILE", state / "league.yml"),
    ):
        if os.environ.get(name) != str(expected):
            raise TrustProtocolError(
                "deployment certification control state differs from original owner"
            )


def inspect_deployment_certification_readiness(
    *,
    fleet_provisioner: object,
    fleet_run_id: str,
) -> Mapping[str, str | int]:
    """M211: public, read-only same-process preflight for deployment integrators.

    Returns the original provisioner's existing OBSERVED_NOT_ADMITTED projection.
    Never builds custody, runs bootstrap, acquires certification state, or grants
    permission to run. APP PASS requires a separate actual certification call and
    independently persisted proof after final native Run admission.
    """
    owner = _require_deployment_process_owner(
        fleet_provisioner=fleet_provisioner,
        fleet_run_id=fleet_run_id,
    )
    return owner.inspect_deployment_readiness(fleet_run_id)


def certify_deployment_run(*, fleet_provisioner: object, fleet_run_id: str) -> int:
    """Explicit deployment-owned in-process APP certification entrypoint.

    No shell flag, environment reference, delegated re-entry or serialized
    provisioner can cross a process boundary. The caller must own an already
    enrolled original provisioner in *this* process. Static admission and the
    canonical certification state lease still own the execution frontdoor.
    """
    original_owner = _require_deployment_process_owner(
        fleet_provisioner=fleet_provisioner,
        fleet_run_id=fleet_run_id,
    )
    if not _static_source_admission():
        return 2
    # M205 previously checked these only inside the workflow after the
    # certification lease, DB preflight and workspace reconciliation. That
    # allowed an already-wrong deployment selection to mutate control state.
    _require_deployment_control_state()
    # Reobserve the original deployment's custody before acquiring mutable
    # certification state. Observation does not authorize the Run: admission
    # will check all native authority again at the final write cut.
    original_owner.inspect_deployment_readiness(fleet_run_id)
    _require_deployment_control_state()

    from oh_goon.workflow import run_app_for_deployment

    return _workflow_main_with_state_lease(
        ["app"],
        workflow_runner=lambda _args: run_app_for_deployment(
            fleet_run_id=fleet_run_id,
            deployment_context_guard=lambda env: _deployment_context_mismatch(
                env,
                fleet_provisioner=original_owner,
                fleet_run_id=fleet_run_id,
            ),
            deployment_plan_run=lambda args, env: _run_deployment_plan(
                args,
                env,
                fleet_provisioner=original_owner,
                fleet_run_id=fleet_run_id,
            ),
        ),
    )


def _deployment_context_mismatch(
    env: dict[str, str],
    *,
    fleet_provisioner: FleetPreworkProvisioner,
    fleet_run_id: str,
) -> str | None:
    """Recheck original process custody before any League/lock bootstrap work.

    The first M205 readiness observation is not a lease or a lasting proof.
    Signer/inventory changes between that observation and bootstrap cannot
    become an implicit authorization to start a certification Plan.
    """
    # A late cwd mutation cannot redirect certification bootstrap or state
    # while workflow keeps using the earlier import-pinned source root.
    from oh_goon import workflow

    if Path.cwd().resolve() != workflow.ROOT:
        return "Deployment source root differs from imported workflow authority"
    required = ("OH_GOON_DB", "OH_GOON_LEAGUE_FILE")
    if any(not env.get(key) or os.environ.get(key) != env[key] for key in required):
        return "Deployment certification control state differs from canonical bootstrap"
    if (
        os.environ.get(SQLITE_JOURNAL_MODE_ENV) != SQLITE_JOURNAL_DELETE
        or env.get(SQLITE_JOURNAL_MODE_ENV) != SQLITE_JOURNAL_DELETE
    ):
        return "Deployment certification journal mode changed after original selection"
    if os.environ.get(EXECUTORS_FILE_ENV) != str(fleet_provisioner.executor_inventory_file):
        return "Deployment executor inventory differs from original Fleet owner"
    # Original native observer owns live signer, public-root and inventory checks.
    # Readiness is NOT an admission receipt and cannot be transported to CLI.
    fleet_provisioner.inspect_deployment_readiness(fleet_run_id)
    return None


def _canonical_app_handoff_args(args: list[str]) -> bool:
    """Only the fixed internal workflow APP grammar may reach Fleet.

    No caller-provided options, duplicate Run IDs, extra inputs or an alternate
    Plan/lock can replace the precise M205 in-process handoff.
    """
    if (
        type(args) is not list
        or len(args) != 9
        or args[:4] != ["dev", "_run-plan", APP_PLAN, "--repository-id"]
        or args[5] != "--lock-file"
        or args[7:] != ["--wait", "--repair-handoff"]
    ):
        return False
    if any(type(arg) is not str for arg in args):
        return False
    if not args[4] or args[4].startswith("-") or any(c.isspace() for c in args[4]):
        return False
    lock = Path(args[6])
    return lock.is_absolute() and lock.name == "app-certification.goon.lock"


def _run_deployment_plan(
    args: list[str],
    env: dict[str, str],
    *,
    fleet_provisioner: FleetPreworkProvisioner,
    fleet_run_id: str,
) -> tuple[int, str]:
    """Transport original Fleet custody into the same process's internal command.

    The bootstrap-proven database and League identities must already match the
    deployment's process-owned runtime selection. Never update os.environ
    from the child bootstrap result or serialize a Fleet issuer reference.
    """
    if not _canonical_app_handoff_args(args):
        return 2, "Deployment Fleet handoff requires canonical APP certification arguments"
    failure = _deployment_context_mismatch(
        env,
        fleet_provisioner=fleet_provisioner,
        fleet_run_id=fleet_run_id,
    )
    if failure is not None:
        return 2, failure
    from oh_goon.cli import main as cli_main

    # Do not redirect process-global stdout/stderr in a deployment process:
    # a concurrent service thread must never be captured in APP evidence.
    # The original CLI emits its own output; persisted APP success is separately
    # verified by the canonical workflow before any PASS can be reported.
    result = cli_main([*args, "--fleet-run-id", fleet_run_id], fleet_provisioner=fleet_provisioner)
    return result, ""


def _requires_strict_executor(args: list[str]) -> bool:
    """Return whether this frontdoor command can create strict CI/CD workload attempts."""

    return workflow_intent(args) == "release"


def _static_source_admission() -> bool:
    """Reject invalid canonical execution source before delegation or mutable state.

    Native uv may launch a project console script through a base interpreter whose physical
    installation directory differs from the project environment. Consume the shared compiler
    authority in-process rather than guessing any helper executable from ``sys.executable``.
    """

    source_root = Path.cwd()
    try:
        assert_canonical_source_layout(source_root)
    except ValueError as exc:
        print("CERTIFICATION SOURCE ADMISSION FAILED")
        print(str(exc))
        print("No certification state or Fleet Run was mutated.")
        return False

    payload = compile_canonical_goons(source_root)
    if payload["status"] == "PASS":
        print("CANONICAL GOON STATIC ADMISSION")
        print("Status: PASS")
        print(f"Goons: {payload['goon_count']}")
        print(f"Steps: {payload['step_count']}")
        print(f"Requirements: {payload['requirements_identity']}")
        print(f"Execution authority: {payload['execution_authority_identity']}")
        print(
            "Authority closures: "
            f"{payload['authority_closures_proven']}/{payload['authority_closure_count']} proven"
        )
        print(f"Unresolved execution edges: {payload['unresolved_execution_edge_count']}")
        print(
            "Locked package-local executables: "
            f"{payload['package_executable_authority_count']} proven"
        )
        return True
    print("CERTIFICATION STATIC ADMISSION FAILED")
    print(f"Goons: {payload['goon_count']}")
    print(f"Steps: {payload['step_count']}")
    print(f"Requirements: {payload['requirements_identity']}")
    for issue in payload["issues"]:
        location = str(issue.get("goon") or "catalog")
        if issue.get("step_id"):
            location += f"/{issue['step_id']}"
        print(f"- {issue['code']} {location}: {issue['message']}")
        if issue.get("missing_tools"):
            print("  Missing tools: " + ", ".join(issue["missing_tools"]))
        for path in issue.get("authority_paths") or ():
            print("  Required by: " + path)
        for edge in issue.get("unresolved_edges") or ():
            print("  Unresolved: " + edge)
    print("No certification state or Fleet Run was mutated.")
    return False


def _workflow_main(args: list[str]) -> int:
    from oh_goon.workflow import main as workflow_main

    return workflow_main(args)


def _workflow_main_with_state_lease(
    args: list[str],
    *,
    workflow_runner: Callable[[list[str]], int] | None = None,
) -> int:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    source_root = Path.cwd()
    state = certification_state_root(source_root=source_root, state_home=state_home)
    try:
        with certification_state_lease(state):
            try:
                preflight_certification_database_under_lease(
                    source_root=source_root,
                    state_home=state_home,
                )
            except CertificationEnvironmentError as exc:
                print("CERTIFICATION DATABASE PREFLIGHT FAILED")
                print(str(exc))
                print("No certification Goon ran.")
                return 2
            reconciliation = reconcile_certification_run_workspaces(
                state_home=state_home,
                owned_state_root=state,
            )
            if reconciliation.removed:
                print(
                    "CERTIFICATION STORAGE RECONCILIATION "
                    f"removed={len(reconciliation.removed)} abandoned run workspace(s)"
                )
            if reconciliation.refused:
                print(
                    "CERTIFICATION STORAGE RECONCILIATION "
                    f"retained={len(reconciliation.refused)} unproven/busy workspace(s)"
                )
            return _workflow_main(args) if workflow_runner is None else workflow_runner(args)
    except CertificationStateBusy as exc:
        print("CERTIFICATION STATE BUSY")
        print(str(exc))
        print("No certification Goon ran.")
        return 2


def _enter_systemd_delegation(args: list[str], *, current_reason: str) -> int:
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        print(
            certification_executor_unavailable(
                f"Current process cgroup is not usable: {current_reason}\n"
                "systemd-run is not available on PATH, so a delegated user unit cannot be created."
            )
        )
        print("\nNo certification Goon ran.")
        return 2

    env_file = _write_reentry_environment(os.environ)
    ready_file = env_file.with_name(f"{env_file.name}.{secrets.token_hex(6)}.{_SYSTEMD_READY}")
    command = _systemd_reentry_command(
        systemd_run=Path(systemd_run),
        env_file=env_file,
        ready_file=ready_file,
        workflow_args=args,
    )
    try:
        rc, tail = _stream(command)
        if ready_file.is_file():
            return rc
        detail = tail.strip() or "systemd-run --user returned no diagnostic"
        if "CERTIFICATION EXECUTOR UNAVAILABLE" not in detail:
            print(
                "\n"
                + certification_executor_unavailable(
                    "Current process cgroup is not usable:\n"
                    + current_reason
                    + "\n\nsystemd-run --user could not establish the delegated "
                    "certification unit:\n" + _systemd_failure_diagnosis(os.environ, detail)
                )
            )
            print("\nNo certification Goon ran.")
        return rc or 2
    finally:
        env_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


def _certification_process_environment(
    environment: Mapping[str, str], *, require_strict_executor: bool = False
) -> dict[str, str]:
    result = dict(environment)
    if not require_strict_executor:
        strip_strict_executor_authority(result)
    # Certification already has one exact cross-process workflow lease. Use rollback
    # journaling so correctness does not depend on WAL shared-memory/reset semantics.
    result[SQLITE_JOURNAL_MODE_ENV] = SQLITE_JOURNAL_DELETE
    if os.name != "posix":
        return result
    runtime = Path(f"/run/user/{os.getuid()}")
    bus = runtime / "bus"
    if bus.exists() and stat.S_ISSOCK(bus.stat().st_mode):
        result.setdefault("XDG_RUNTIME_DIR", str(runtime))
        result.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
    return result


def _systemd_failure_diagnosis(environment: Mapping[str, str], detail: str) -> str:
    diagnostic = detail.strip() or "systemd-run --user returned no diagnostic"
    if "failed to connect to bus" not in diagnostic.lower():
        return diagnostic

    kernel_release = _read_text(Path("/proc/sys/kernel/osrelease"))
    proc_version = _read_text(Path("/proc/version"))
    is_wsl = "microsoft" in (kernel_release + "\n" + proc_version).lower()
    pid1 = _read_text(Path("/proc/1/comm")).strip() or "unknown"
    runtime = environment.get("XDG_RUNTIME_DIR", "").strip()
    bus_address_present = bool(environment.get("DBUS_SESSION_BUS_ADDRESS", "").strip())

    lines = [diagnostic, "", "systemd user-session diagnosis:", f"- PID 1: {pid1}"]
    if is_wsl:
        lines.append("- environment: WSL detected")
        lines.append(f"- /etc/wsl.conf [boot] systemd: {_wsl_systemd_setting()}")
    else:
        lines.append("- environment: WSL not detected")

    if runtime:
        bus = Path(runtime) / "bus"
        lines.append(f"- XDG_RUNTIME_DIR: {runtime}")
        lines.append(f"- systemd user bus: {_socket_status(bus)} ({bus})")
    else:
        lines.append("- XDG_RUNTIME_DIR: not set")
        lines.append("- systemd user bus: cannot be derived")
    lines.append("- DBUS_SESSION_BUS_ADDRESS: " + ("set" if bus_address_present else "not set"))

    if is_wsl and pid1 != "systemd":
        lines.extend(
            [
                "",
                "WSL recovery:",
                "1. Ensure /etc/wsl.conf contains [boot] with systemd=true.",
                "2. Fully terminate WSL from Windows with: wsl.exe --shutdown",
                "3. Reopen the distribution and verify: systemctl status",
                "4. Rerun: make certify",
            ]
        )
    elif pid1 == "systemd":
        lines.extend(
            [
                "",
                "systemd is PID 1, but the certification user manager is not ready.",
                "One-time setup for strict execution: make strict-executor-setup",
                "Verify: systemctl --user status",
                "Then retry the same strict release/CI command.",
            ]
        )
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _wsl_systemd_setting() -> str:
    path = Path("/etc/wsl.conf")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "not configured (file missing)"
    except OSError as exc:
        return f"unreadable ({exc})"

    section = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "boot" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == "systemd":
            normalized = value.split("#", 1)[0].split(";", 1)[0].strip().lower()
            return normalized or "empty"
    return "not configured"


def _socket_status(path: Path) -> str:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable ({exc})"
    return "socket present" if stat.S_ISSOCK(mode) else "present but not a socket"


def _systemd_reentry_command(
    *,
    systemd_run: Path,
    env_file: Path,
    ready_file: Path,
    workflow_args: list[str],
) -> list[str]:
    return [
        str(systemd_run),
        "--user",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--property=Type=exec",
        "--property=Delegate=yes",
        f"--working-directory={Path.cwd().resolve()}",
        "--",
        sys.executable,
        "-I",
        "-m",
        "oh_goon.services.certification_frontdoor",
        _REENTRY_FLAG,
        str(env_file),
        str(ready_file),
        *workflow_args,
    ]


def _run_delegated_reentry(args: list[str]) -> int:
    if len(args) < 2:
        print(certification_executor_unavailable("invalid internal systemd re-entry arguments"))
        print("\nNo certification Goon ran.")
        return 2
    env_file = Path(args[0])
    ready_file = Path(args[1])
    workflow_args = args[2:]
    if not _requires_strict_executor(workflow_args):
        print(
            certification_executor_unavailable(
                "internal delegated re-entry is valid only for an explicit strict release/CI workflow"
            )
        )
        print("\nNo certification Goon ran.")
        return 2
    try:
        restored = _read_secure_environment(env_file)
    except (OSError, ValueError) as exc:
        print(
            certification_executor_unavailable(f"cannot restore certification environment: {exc}")
        )
        print("\nNo certification Goon ran.")
        return 2

    os.environ.clear()
    os.environ.update(restored)
    os.environ[SYSTEMD_DELEGATED_ENV] = "1"
    os.environ[SYSTEMD_SUBGROUP_ENV] = _DELEGATE_SUBGROUP
    try:
        root = resolve_certification_cgroup_root(os.environ.copy())
    except FleetPlanInvalid as exc:
        print(
            certification_executor_unavailable(
                "systemd created the transient user unit, but its delegated cgroup is not usable: "
                + str(exc)
            )
        )
        print("\nNo certification Goon ran.")
        return 2

    try:
        _publish_ready(ready_file, root)
    except OSError as exc:
        print(certification_executor_unavailable(f"cannot publish delegation readiness: {exc}"))
        print("\nNo certification Goon ran.")
        return 2
    return _workflow_main_with_state_lease(workflow_args)


def _write_reentry_environment(environment: Mapping[str, str]) -> Path:
    runtime = environment.get("XDG_RUNTIME_DIR", "").strip()
    directory: str | None = None
    if runtime:
        candidate = Path(runtime)
        if candidate.is_absolute() and candidate.is_dir() and os.access(candidate, os.W_OK):
            directory = str(candidate)
    fd, raw_path = tempfile.mkstemp(
        prefix=".oh-goon-certification-env-",
        suffix=".json",
        dir=directory,
        text=True,
    )
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(dict(environment), stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _read_secure_environment(path: Path) -> dict[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(
                f"re-entry environment file is not private regular user-owned state: {path}"
            )
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            payload = json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("re-entry environment payload is not a string environment mapping")
    return payload


def _publish_ready(path: Path, root: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(str(root.resolve()) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _stream(args: list[str]) -> tuple[int, str]:
    tail: list[str] = []

    def observe(_stream: str, text: str) -> None:
        print(text, end="")
        tail.append(text)
        if len(tail) > 200:
            tail.pop(0)

    result = run_command(
        *args,
        timeout=None,
        stream_output=observe,
        merge_stderr=True,
    )
    return result.returncode, "".join(tail)


if __name__ == "__main__":
    raise SystemExit(main())
