from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.repository_tooling import certification_bootstrap_protocol

ROOT = Path.cwd().resolve()
APP_PLAN = "templates/plans/oh-goon/app-certification/plan.yml"
SELF_VERIFY_PLAN = "templates/plans/oh-goon/self-verification/plan.yml"
RELEASE_PLAN = "templates/plans/oh-goon/prepare-release/plan.yml"

SELF_PLAN_COMMANDS: dict[str, dict[str, str]] = {
    "verify": {
        "plan": SELF_VERIFY_PLAN,
        "label": "self-verification",
        "success": "SELF VERIFICATION PASSED",
        "final_command": "oh-goon self verify",
    },
    "certify": {
        "plan": APP_PLAN,
        "label": "APP certification",
        "success": "APP CERTIFICATION PASSED",
        "final_command": "oh-goon self certify",
    },
}

BOOTSTRAP_FAILURE_PREFIX = "CERTIFICATION PLAN BOOTSTRAP FAILED"
CERTIFICATION_CONTEXT_FAILURE_PREFIX = "CERTIFICATION CONTEXT FAILED"
CERTIFICATION_DEPENDENCY_PREFIX = "OH_GOON_CERTIFICATION_DEPENDENCIES="
CERTIFICATION_DEPENDENCY_ERROR_PREFIX = "OH_GOON_CERTIFICATION_DEPENDENCIES_ERROR="
CERTIFICATION_ACTIVITY_PREFIX = "OH-GOON ACTIVITY:"
_PRODUCT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

APP_FORBIDDEN_ACQUISITION_HINTS = (
    "failed to download",
    "failed to fetch",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "playwright download",
    "browser download",
)

ENVIRONMENT_HINTS = (
    "temporary failure in name resolution",
    "name or service not known",
    "could not resolve host",
    "certificate verify failed",
    "unable to get local issuer certificate",
    "network is unreachable",
    "connection timed out",
    "econnrefused",
    "enotfound",
    "eai_again",
    "econnreset",
    "failed to download",
    "failed to fetch",
    "proxy error",
    "proxy connection",
    "cannot allocate memory",
    "out of memory",
    "input/output error",
    "[errno 5]",
    "read-only file system",
    "[errno 30]",
    "unable to open database file",
    "attempt to write a readonly database",
    "attempt to write a read-only database",
    "database or disk is full",
    "disk i/o error",
    "readonly database",
    "host system is missing dependencies",
    "browser dependencies are missing",
    "uv check failed: missing",
    "node check failed: missing",
    "npm check failed: missing",
)


def _run_capture(
    args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


_CANCEL_GRACE_SECONDS = 1.0


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_process_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def _wait_for_process_group_exit(proc: subprocess.Popen[str], pgid: int) -> bool:
    deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
    while time.monotonic() < deadline:
        proc.poll()  # reap the leader promptly without treating leader exit as group exit
        if not _process_group_exists(pgid):
            return True
        time.sleep(0.01)
    return not _process_group_exists(pgid)


def _terminate_owned_process_group(
    proc: subprocess.Popen[str], *, first_signal: int = signal.SIGINT
) -> None:
    """Boundedly stop the exact process group created for one workflow child."""

    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
        try:
            proc.wait(timeout=_CANCEL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=_CANCEL_GRACE_SECONDS)
        return

    pgid = proc.pid
    for sig in (first_signal, signal.SIGTERM):
        if not _signal_process_group(pgid, sig) or _wait_for_process_group_exit(proc, pgid):
            return
    _signal_process_group(pgid, signal.SIGKILL)
    try:
        proc.wait(timeout=_CANCEL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _stream(args: list[str], *, env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=(os.name != "nt"),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    try:
        for line in proc.stdout:
            print(line, end="")
            tail.append(line)
            if len(tail) > 300:
                tail.pop(0)
        return proc.wait(), "".join(tail)
    except KeyboardInterrupt:
        _terminate_owned_process_group(proc)
        raise
    finally:
        if proc.poll() is None:
            _terminate_owned_process_group(proc, first_signal=signal.SIGTERM)


def _classify(rendered: str) -> str:
    lowered = rendered.lower()
    if "modulenotfounderror" in lowered and "oh_goon." in lowered:
        return "application"
    # A concrete terminal source/build contract defect outranks generic environment-like
    # text elsewhere in the retained tail. Hatch forced-inclusion failures mean the
    # certifiable workspace is structurally incomplete; broadening or repairing the
    # host environment cannot make that source contract correct.
    if "forced include not found" in lowered:
        return "application"
    # Once pytest has emitted terminal failure evidence, the failed test/fixture is the
    # owning repair boundary. Environment-looking text inside captured test output must
    # not reclassify an assertion/test failure as a host prerequisite blocker.
    lines = lowered.splitlines()
    if any(line.strip(" =") == "failures" for line in lines) or any(
        line.startswith("failed ") and "::" in line for line in lines
    ):
        return "application"
    return "environment" if any(hint in lowered for hint in ENVIRONMENT_HINTS) else "application"


def _app_attempted_dependency_acquisition(rendered: str) -> bool:
    lowered = rendered.lower()
    return any(hint in lowered for hint in APP_FORBIDDEN_ACQUISITION_HINTS)


def _certification_product_version() -> str:
    """Read the one checkout-owned product/checkpoint version.

    The certification frontdoor is launched from the materialized ``backend/.venv`` that
    native uv prepared. Package-manager version
    fields are package/release metadata and are deliberately not mirrored into the
    development checkpoint identity.
    """

    version_file = ROOT / "VERSION"
    try:
        if version_file.is_symlink() or not version_file.is_file():
            raise RuntimeError("VERSION must be a regular non-symlink file")
        value = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read checkout VERSION: {exc}") from exc
    if _PRODUCT_VERSION.fullmatch(value) is None:
        raise RuntimeError(f"VERSION must contain canonical x.y.z product identity, got {value!r}")
    return value


def _backend_cli_executable() -> str:
    """Return the sibling CLI from the exact materialized environment running this workflow."""
    return str(Path(os.path.abspath(sys.executable)).parent / "oh-goon")


def _transient_activity(message: str) -> None:
    """Emit bounded, path-free certification progress outside durable evidence when possible."""

    rendered = message.strip()
    if not rendered:
        return
    configured = os.environ.get("OH_GOON_PROGRESS_FD")
    if configured is not None:
        try:
            fd = int(configured)
            if fd < 0:
                raise ValueError
            os.write(fd, f"Certification activity: {rendered}\n".encode("utf-8", errors="replace"))
            return
        except (OSError, ValueError):
            pass
    print(f"Certification activity: {rendered}", flush=True)


def _backend_cli(
    args: list[str],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Capture backend protocol while forwarding only its bounded transient activity live."""

    command = [_backend_cli_executable(), *args]
    try:
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            start_new_session=(os.name != "nt"),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            command, 127, stdout=f"Cannot run {' '.join(command)}: {exc}\n"
        )
    assert proc.stdout is not None
    lines: list[str] = []
    try:
        for line in proc.stdout:
            lines.append(line)
            if line.startswith(CERTIFICATION_ACTIVITY_PREFIX):
                _transient_activity(line[len(CERTIFICATION_ACTIVITY_PREFIX) :])
        return subprocess.CompletedProcess(command, proc.wait(), stdout="".join(lines))
    except KeyboardInterrupt:
        _terminate_owned_process_group(proc)
        raise
    finally:
        if proc.poll() is None:
            _terminate_owned_process_group(proc, first_signal=signal.SIGTERM)


class CertificationContextFailure(RuntimeError):
    def __init__(self, message: str, *, classification: str) -> None:
        super().__init__(message)
        self.classification = classification


def _certification_environment(
    *, require_strict_executor: bool = False
) -> tuple[str, dict[str, str], Path]:
    """Ask bootstrap for control state and only workflow-consumed execution authority."""
    env = os.environ.copy()
    if not require_strict_executor:
        # APP/self verification is direct execution. The workflow runner stays outside
        # runtime-domain authority and consumes only the dependency-neutral strict
        # environment vocabulary. Frontdoor/bootstrap repeat the fail-closed strip.
        env.pop(EXECUTORS_FILE_ENV, None)
        for name in tuple(env):
            if name.startswith(("OH_GOON_CERTIFICATION_CGROUP_", "OH_GOON_CERTIFICATION_SYSTEMD_")):
                env.pop(name, None)
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    command = ["dev", "bootstrap-certification", "--state-root", str(state_home)]
    if require_strict_executor:
        command.append("--require-strict-executor")
    _transient_activity(
        "validating durable control state and strict executor readiness"
        if require_strict_executor
        else "validating durable control state"
    )
    result = _backend_cli(command, env=env)
    if result.returncode != 0:
        error_payloads = certification_bootstrap_protocol.error_payloads(result.stdout)
        classification = "application"
        message = "backend certification bootstrap returned an invalid error response"
        if len(error_payloads) == 1:
            try:
                error = json.loads(error_payloads[0])
                classification = str(error["classification"])
                message = str(error["message"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        rendered_command = "oh-goon " + " ".join(command)
        raise CertificationContextFailure(
            f"{CERTIFICATION_CONTEXT_FAILURE_PREFIX}\nCommand: {rendered_command}\n{message}",
            classification=classification,
        )

    payload_lines = certification_bootstrap_protocol.success_payloads(result.stdout)
    if len(payload_lines) != 1:
        raise CertificationContextFailure(
            f"{CERTIFICATION_CONTEXT_FAILURE_PREFIX}\n"
            "backend certification bootstrap returned an invalid success response",
            classification="application",
        )
    try:
        payload = json.loads(payload_lines[0])
        repo_id = str(payload["repository_id"])
        database = str(payload["database"])
        league_file = str(payload["league_file"])
        lock_root = Path(str(payload["lock_root"]))
        executors_file = str(payload.get("executors_file", "")).strip()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CertificationContextFailure(
            f"{CERTIFICATION_CONTEXT_FAILURE_PREFIX}\n"
            "backend certification bootstrap returned an invalid success response",
            classification="application",
        ) from exc
    if not all((repo_id, database, league_file)) or not lock_root.is_absolute():
        raise CertificationContextFailure(
            f"{CERTIFICATION_CONTEXT_FAILURE_PREFIX}\n"
            "backend certification bootstrap returned incomplete process authority",
            classification="application",
        )
    if require_strict_executor != bool(executors_file):
        raise CertificationContextFailure(
            f"{CERTIFICATION_CONTEXT_FAILURE_PREFIX}\n"
            "backend certification bootstrap returned mismatched strict executor authority",
            classification="application",
        )
    env.update(
        {
            "OH_GOON_DB": database,
            "OH_GOON_LEAGUE_FILE": league_file,
        }
    )
    if executors_file:
        env[EXECUTORS_FILE_ENV] = executors_file
    _transient_activity(
        "durable control state and strict executor readiness verified"
        if require_strict_executor
        else "durable control state verified"
    )
    return repo_id, env, lock_root


def _fail_bootstrap(
    output: str,
    *,
    message: str,
    final_command: str = "make certify",
    failure_class: str | None = None,
) -> int:
    print(output, end="" if output.endswith("\n") or not output else "\n")
    classification = failure_class or _classify(output)
    if classification == "environment":
        print("\nBLOCKED BY ENVIRONMENT")
        print(message)
        print(f"Fix the environment cause, then rerun: {final_command}")
    elif classification == "authority":
        print("\nAUTHENTIC SOURCE AUTHORITY REQUIRED")
        print(message)
        print(
            "Restore the exact canonical source/checkpoint bytes that own this operation; "
            "ordinary certification never manufactures missing source authority."
        )
        print(f"Then rerun: {final_command}")
    else:
        print("\nAPPLICATION FAILED")
        print(message)
        print("This is a source/bootstrap defect, not an environment failure.")
        print(
            f"Give the complete output above to the coding agent, then rerun: {final_command} after repair."
        )
    print("No certification Goon ran.")
    return 2


def _plan_input_args(inputs: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key in sorted(inputs):
        args.extend(["--input", f"{key}={inputs[key]}"])
    return args


def _plan_lock_path(lock_root: Path, plan: str) -> Path:
    """Return the isolated certification lock owned for one canonical Plan."""
    return lock_root / f"{Path(plan).parent.name}.goon.lock"


def _run_plan(
    *,
    plan: str,
    repo_id: str,
    env: dict[str, str],
    lock_root: Path,
    inputs: dict[str, str] | None = None,
    deployment_plan_run: Callable[[list[str], dict[str, str]], tuple[int, str]] | None = None,
    deployment_context_guard: Callable[[dict[str, str]], str | None] | None = None,
) -> tuple[int, str]:
    input_args = _plan_input_args(inputs or {})
    lock_path = _plan_lock_path(lock_root, plan)
    if deployment_plan_run is not None:
        # Deployment APP has no user inputs. Reject these before League discovery
        # or lock publication, rather than waiting for the Fleet CLI to refuse
        # the altered argv after those bootstrap side effects.
        if plan != APP_PLAN or deployment_context_guard is None or inputs:
            return 2, "Deployment Fleet handoff requires canonical APP without inputs"
        failure = deployment_context_guard(env)
        if failure is not None:
            return 2, failure
    bootstrap_commands = (
        ["league", "add-folder", str(ROOT)],
        ["lock", plan, *input_args, "--output", str(lock_path)],
    )
    for command in bootstrap_commands:
        result = _backend_cli(command, env=env)
        if result.returncode != 0:
            rendered_command = "oh-goon " + " ".join(command)
            output = f"{BOOTSTRAP_FAILURE_PREFIX}\nCommand: {rendered_command}\n{result.stdout}"
            return result.returncode, output

    plan_args = [
        "plan",
        "run",
        plan,
        "--repository-id",
        repo_id,
        *input_args,
        "--lock-file",
        str(lock_path),
        "--wait",
        "--repair-handoff",
    ]
    if deployment_plan_run is not None:
        # Explicit same-process deployment handoff: only APP, never verify or
        # release, and no serialized credentials or subprocess Plan execution.
        if plan != APP_PLAN:
            return 2, "Deployment Fleet handoff only supports canonical APP certification"
        return deployment_plan_run(plan_args, env)
    return _stream([_backend_cli_executable(), *plan_args], env=env)


def _require_current_app_pass(
    *,
    repo_id: str,
    env: dict[str, str],
    lock_root: Path,
    fleet_run_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Ask the backend evidence owner whether current source has canonical APP PASS."""
    return _backend_cli(
        [
            "dev",
            "require-plan-success",
            "--plan",
            APP_PLAN,
            "--repository-id",
            repo_id,
            "--source-root",
            str(ROOT),
            "--lock-file",
            str(_plan_lock_path(lock_root, APP_PLAN)),
            *(["--fleet-run-id", fleet_run_id] if fleet_run_id is not None else []),
        ],
        env=env,
    )


def _prepare_plan_context(*, command: str) -> tuple[str, dict[str, str], Path] | int:
    descriptor = SELF_PLAN_COMMANDS[command]
    final_command = descriptor["final_command"]
    try:
        return _certification_environment()
    except CertificationContextFailure as exc:
        return _fail_bootstrap(
            str(exc),
            message=f"Backend certification bootstrap could not prepare {descriptor['label']} process authority.",
            final_command=final_command,
            failure_class=exc.classification,
        )


def _run_self_plan(
    command: str,
    *,
    deployment_plan_run: Callable[[list[str], dict[str, str]], tuple[int, str]] | None = None,
    deployment_context_guard: Callable[[dict[str, str]], str | None] | None = None,
    deployment_fleet_run_id: str | None = None,
) -> int:
    # A deployment callback without exact enrolled identity could otherwise
    # accept a stale, source-matching APP PASS from another completed Run.
    if deployment_plan_run is not None and (
        type(deployment_fleet_run_id) is not str
        or not deployment_fleet_run_id.strip()
        or deployment_fleet_run_id != deployment_fleet_run_id.strip()
    ):
        print("Deployment APP requires exact enrolled Fleet Run identity before bootstrap")
        return 2
    if deployment_plan_run is None and deployment_fleet_run_id is not None:
        print("Fleet Run identity cannot be supplied without original deployment owner")
        return 2
    descriptor = SELF_PLAN_COMMANDS[command]
    prepared = _prepare_plan_context(command=command)
    if isinstance(prepared, int):
        return prepared
    repo_id, env, lock_root = prepared
    print(f"\nStarting canonical {descriptor['label']} Plan...", flush=True)
    # Bind the in-process Plan callback to the *one* repository and lock path
    # returned by the original backend certification bootstrap. Shape-only
    # checking in the Fleet frontdoor cannot establish this identity: another
    # absolute lock path with the same filename is still an untrusted path.
    bound_deployment_run: Callable[[list[str], dict[str, str]], tuple[int, str]] | None = None
    if deployment_plan_run is not None:
        deployment_owner = deployment_plan_run
        expected_args = [
            "plan",
            "run",
            APP_PLAN,
            "--repository-id",
            repo_id,
            "--lock-file",
            str(_plan_lock_path(lock_root, APP_PLAN)),
            "--wait",
            "--repair-handoff",
        ]

        def guarded_deployment_run(args: list[str], child_env: dict[str, str]) -> tuple[int, str]:
            if args != expected_args:
                return 2, "Deployment APP repository or lock differs from canonical bootstrap"
            return deployment_owner(args, child_env)

        bound_deployment_run = guarded_deployment_run

    rc, tail = _run_plan(
        plan=descriptor["plan"],
        repo_id=repo_id,
        env=env,
        lock_root=lock_root,
        inputs={},
        deployment_plan_run=bound_deployment_run,
        deployment_context_guard=deployment_context_guard,
    )

    if rc == 0 and deployment_plan_run is not None:
        # Re-check after the callback: a deployment process may mutate cwd,
        # selected control state, inventory or custody while its Plan is running.
        # Do not consult persisted APP evidence under a different context.
        if deployment_context_guard is None:
            print("Deployment APP context guard is required before persisted evidence")
            return 2
        context_failure = deployment_context_guard(env)
        if context_failure is not None:
            print(f"Deployment APP context changed before persisted evidence: {context_failure}")
            return 2
        # The in-process callback is transport, not APP PASS authority. The
        # canonical persisted success verifier must independently agree.
        confirmed = _require_current_app_pass(
            repo_id=repo_id,
            env=env,
            lock_root=lock_root,
            fleet_run_id=deployment_fleet_run_id,
        )
        if confirmed.returncode != 0:
            print("APP certification record is not verified for this exact source and Run")
            return 2
    if rc == 0:
        print(f"\n{descriptor['success']}")
        if command == "certify":
            print(
                "APP certification passed for this exact checkout and native locked dependency materialization."
            )
        return 0
    if tail.startswith(BOOTSTRAP_FAILURE_PREFIX):
        return _fail_bootstrap(
            tail,
            message=f"{descriptor['label'].capitalize()} bootstrap failed before any Plan Goon ran.",
            final_command=descriptor["final_command"],
        )
    if command == "certify":
        return _render_app_failure(rc=rc, tail=tail)
    return rc or 1


def run_app() -> int:
    try:
        product_version = _certification_product_version()
    except RuntimeError as exc:
        return _fail_bootstrap(
            str(exc),
            message="Oh-Goon could not prove the checkout product version used for certification.",
        )
    print("OH-GOON CERTIFICATION IDENTITY")
    print(f"Product version: {product_version}", flush=True)
    return _run_self_plan("certify")


def run_app_for_deployment(
    *,
    deployment_plan_run: Callable[[list[str], dict[str, str]], tuple[int, str]],
    deployment_context_guard: Callable[[dict[str, str]], str | None],
    fleet_run_id: str,
) -> int:
    """Run the canonical APP Plan through an explicit deployment process callback.

    The original Fleet CLI remains the execution authority; workflow only
    orchestrates bootstrap, lock, Plan execution and the APP result. This
    callback is never populated by the ordinary shell certification frontdoor.
    """
    try:
        product_version = _certification_product_version()
    except RuntimeError as exc:
        return _fail_bootstrap(
            str(exc),
            message="Oh-Goon could not prove the checkout product version used for certification.",
        )
    print("OH-GOON CERTIFICATION IDENTITY")
    print(f"Product version: {product_version}", flush=True)
    return _run_self_plan(
        "certify",
        deployment_plan_run=deployment_plan_run,
        deployment_context_guard=deployment_context_guard,
        deployment_fleet_run_id=fleet_run_id,
    )


def run_self_verify() -> int:
    rc = _run_self_plan("verify")
    if rc == 0:
        print("OH_GOON_SELF_VERIFICATION_STATUS=COMPLETE")
    else:
        print("OH_GOON_SELF_VERIFICATION_STATUS=INCOMPLETE")
        print(
            "Self-verification execution did not complete successfully; missing proof is not a test failure."
        )
    return rc


def self_plan_descriptor(command: str) -> dict[str, object]:
    """Project the one canonical self-command registry; do not re-resolve Plan ownership here."""
    descriptor = SELF_PLAN_COMMANDS[command]
    return {
        "command": descriptor["final_command"],
        "plan": descriptor["plan"],
        "execution_authority": "FleetPlanService.run",
        "pipeline": ["resolve", "lock", "admit", "execute", "evidence"],
    }


def _render_app_failure(*, rc: int, tail: str) -> int:
    if (
        rc == 2
        and "Fleet Run ID:" not in tail
        and (certification_bootstrap_protocol.is_missing_fleet_provisioning(tail))
    ):
        # The original runtime lifecycle owns this denial. Certification only
        # reports it; it must never substitute a key, issuer, executor or
        # enrollment to turn a missing deployment authority into APP PASS.
        print("\nBLOCKED BY FLEET ENFORCER PROVISIONING")
        print("An exact Fleet Run has no explicitly enrolled native Enforcer authority.")
        print(
            "Provision the Run through the deployment's original FleetPreworkProvisioner "
            "with trusted public roots, native issuer and executor inventory evidence."
        )
        print("No default signing keys or fallback issuer will be created.")
        print("No certification Goon ran. APP PASS and release remain blocked.")
        print(certification_bootstrap_protocol.render_missing_fleet_provisioning_blocker())
    elif rc == 2 and "Fleet Run ID:" not in tail:
        print("\nAPPLICATION FAILED")
        print("APP certification was rejected before any certification Goon ran.")
        print(
            "Give the complete output above to the coding agent, then rerun: "
            "make certify after repair."
        )
    elif _classify(tail) == "environment":
        print("\nBLOCKED BY ENVIRONMENT")
        print("A certification prerequisite failed because of the runtime environment.")
        print(
            "Use the canonical focused diagnostic command from the failure block above "
            "for the observed runtime/environment problem."
        )
        print("Fix the reported environment cause, then rerun: make certify")
        print("The handoff retains the canonical failing command for focused diagnosis only.")
    else:
        print("\nAPPLICATION FAILED")
        print("Give the complete CERTIFICATION PLAN FAILED block above to the coding agent.")
        print("The handoff retains the canonical failing command for focused diagnosis.")
        print("After repair, rerun: make certify")
    return rc or 1


def run_release() -> int:
    final_command = "make prepare-release"
    try:
        product_version = _certification_product_version()
        repo_id, env, lock_root = _certification_environment(require_strict_executor=True)
    except (CertificationContextFailure, RuntimeError) as exc:
        return _fail_bootstrap(
            str(exc),
            message="Backend certification bootstrap could not prepare release process authority.",
            final_command=final_command,
            failure_class=getattr(exc, "classification", "application"),
        )
    print(f"Release version: {product_version}", flush=True)
    return _run_release_with_certification_state(
        repo_id=repo_id,
        env=env,
        lock_root=lock_root,
    )


def _run_release_with_certification_state(
    *, repo_id: str, env: dict[str, str], lock_root: Path
) -> int:
    app_pass = _require_current_app_pass(repo_id=repo_id, env=env, lock_root=lock_root)
    if app_pass.returncode != 0:
        if app_pass.returncode == 3:
            print("PACKAGE BLOCKED: current filtered source identity has no canonical APP PASS.")
            print(
                "Run make certify and require APP PASS for this exact checkout before "
                "package production."
            )
            return 2
        return _fail_bootstrap(
            app_pass.stdout,
            message="Oh-Goon could not verify canonical APP PASS before package production.",
            final_command="make certify",
        )
    print(f"Release target: {repo_id}")
    print("\nStarting canonical prepare-release Plan...\n", flush=True)
    rc, tail = _run_plan(
        plan=RELEASE_PLAN,
        repo_id=repo_id,
        env=env,
        lock_root=lock_root,
        inputs={},
    )
    if rc == 2 and "Fleet Run ID:" not in tail:
        print("\nPACKAGE ADMISSION FAILED")
        print("No release Goon ran; give the complete error above to the coding agent.")
    return rc or 0


def run_explain(fleet_run_id: str) -> int:
    if not fleet_run_id.strip():
        print("OH_GOON_RUN_ID=<fleet-run-id> is required", file=sys.stderr)
        return 2
    try:
        _, env, _ = _certification_environment()
    except CertificationContextFailure as exc:
        return _fail_bootstrap(
            str(exc),
            message="Backend certification bootstrap could not prepare evidence process authority.",
            final_command=f"make certification-explain OH_GOON_RUN_ID={fleet_run_id}",
            failure_class=exc.classification,
        )
    result = _backend_cli(["dev", "explain-run", fleet_run_id], env=env)
    print(result.stdout, end="")
    return result.returncode


def workflow_intent(argv: list[str]) -> Literal["app", "verify", "release", "explain"] | None:
    """Classify the canonical workflow grammar without observing or changing state."""
    if not argv or argv == ["app"]:
        return "app"
    if argv == ["verify"]:
        return "verify"
    if argv == ["release"]:
        return "release"
    if len(argv) == 2 and argv[0] == "explain" and argv[1].strip():
        return "explain"
    return None


def main(argv: list[str] | None = None) -> int:
    # An explicitly supplied empty list means "run the default APP workflow".
    # Do not fall back to this process's argv: under the systemd certification
    # frontdoor those argv entries are internal re-entry protocol arguments.
    args = list(sys.argv[1:] if argv is None else argv)
    intent = workflow_intent(args)
    if intent == "app":
        return run_app()
    if intent == "verify":
        return run_self_verify()
    if intent == "release":
        return run_release()
    if intent == "explain":
        return run_explain(args[1])
    print("usage: oh-goon-workflow [app|verify|release] | explain <fleet-run-id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
