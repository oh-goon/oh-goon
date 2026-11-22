"""Explicit disposable native environment for two-Run qualification, never fallback."""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.engine.command import CommandResult, run_command


@dataclass(frozen=True, slots=True)
class NativeConcurrencyEnvironment:
    """One selected test slice, not an application execution/control owner."""

    policy: RunConcurrencyPolicy
    systemd_run: str
    systemctl: str
    manager_environment: tuple[tuple[str, str], ...] = field(repr=False)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 180,
    ) -> CommandResult:
        # The test explicitly selects a new environment. It does not claim the
        # old caller's private limits were inherited or bypass product admission.
        # Unlimited *local* controls retain every finite shared-slice ceiling.
        unit = f"oh-goon-h223-controller-{uuid4().hex}.scope"
        selected_environment = dict(environment) if environment is not None else dict(os.environ)
        selected_environment.update(dict(self.manager_environment))
        return run_command(
            self.systemd_run,
            "--user",
            "--scope",
            "--quiet",
            f"--unit={unit}",
            f"--slice={self.policy.aggregate_slice}",
            "--property=CPUQuota=",
            "--property=CPUWeight=100",
            "--property=MemoryMax=infinity",
            "--property=MemorySwapMax=infinity",
            "--property=TasksMax=infinity",
            "--property=MemoryAccounting=yes",
            "--property=TasksAccounting=yes",
            "--",
            *argv,
            cwd=cwd,
            env=selected_environment,
            timeout=timeout,
            max_capture_bytes_per_stream=1024 * 1024,
            require_process_group_quiescence=True,
        )


@contextmanager
def selected_native_concurrency_environment(
    *,
    aggregate_resources: CicdResourcePolicy,
    worker_resources: CicdResourcePolicy,
    controller_reserve: CicdResourcePolicy,
) -> Generator[NativeConcurrencyEnvironment, None, None]:
    """Use native runtime properties and exact UUID names; never repair the host.

    This is a named native qualification setup, not ordinary collection/setup
    and not a second Run executor. Callers run the original application/tests in
    the selected environment. Missing manager/controllers fail the qualification.
    """
    tools = tuple(shutil.which(name) for name in ("systemd-run", "systemctl"))
    if any(value is None for value in tools):
        raise RuntimeError("Selected native concurrency environment requires systemd user tools")
    systemd_run, systemctl = (str(Path(value).resolve(strict=True)) for value in tools if value)
    unit = f"ohgoonh223{uuid4().hex}.slice"
    policy = RunConcurrencyPolicy(
        aggregate_slice=unit,
        aggregate_resources=aggregate_resources,
        worker_resources=worker_resources,
        controller_reserve=controller_reserve,
    )
    assert aggregate_resources.cpu_millis is not None
    assert aggregate_resources.memory_bytes is not None
    assert aggregate_resources.max_processes is not None
    whole, fractional = divmod(aggregate_resources.cpu_millis, 10)
    quota = str(whole) if fractional == 0 else f"{whole}.{fractional}"
    manager_environment = tuple(
        (name, os.environ[name])
        for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
        if name in os.environ
    )
    primary: BaseException | None = None
    try:
        prepare = run_command(
            systemctl,
            "--user",
            "set-property",
            "--runtime",
            unit,
            f"CPUQuota={quota}%",
            f"MemoryMax={aggregate_resources.memory_bytes}",
            "MemorySwapMax=0",
            f"TasksMax={aggregate_resources.max_processes}",
            env=dict(manager_environment),
            timeout=10,
            max_capture_bytes_per_stream=8192,
            require_process_group_quiescence=True,
        )
        if prepare.returncode != 0:
            raise RuntimeError("Selected native concurrency slice could not be provisioned")
        yield NativeConcurrencyEnvironment(policy, systemd_run, systemctl, manager_environment)
    except BaseException as error:
        primary = error
        raise
    finally:
        failures = []
        for operation in ("stop", "revert"):
            try:
                result = run_command(
                    systemctl,
                    "--user",
                    operation,
                    unit,
                    env=dict(manager_environment),
                    timeout=10,
                    max_capture_bytes_per_stream=8192,
                    require_process_group_quiescence=True,
                )
                if result.returncode != 0:
                    failures.append(f"Native concurrency slice {operation} was incomplete")
                    # Retain the native ceilings if stopping could not be
                    # established; reverting them could unbound live work.
                    break
            except Exception:
                failures.append(f"Native concurrency slice {operation} was incomplete")
                break
        if failures:
            if primary is not None:
                for failure in failures:
                    primary.add_note(failure)
            else:
                raise RuntimeError("; ".join(failures))
