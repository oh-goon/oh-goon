from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.integrations._fleet_plan_cgroup_resources import NESTED_CGROUP_DELEGATED_ENV
from oh_goon.repository_tooling.acceptance.timing import (
    RESOLVED_TIMING_ENV,
    RuntimeAcceptanceTiming,
)
from oh_goon.services.certification_bootstrap import (
    SYSTEMD_DELEGATED_ENV,
    SYSTEMD_SUBGROUP_ENV,
    resolve_certification_cgroup_root,
)

LOCAL_RUNTIME_DELEGATED_ENV = "OH_GOON_LOCAL_RUNTIME_SYSTEMD_DELEGATED"
LOCAL_RUNTIME_PROCESS_SUBGROUP = "oh-goon-local-runtime-process"


def needs_systemd_delegation(environment: dict[str, str]) -> bool:
    if os.name != "posix" or environment.get(LOCAL_RUNTIME_DELEGATED_ENV) == "1":
        return False
    if environment.get(NESTED_CGROUP_DELEGATED_ENV) == "1":
        # Nested strict execution is already inside an explicitly delegated Fleet
        # authority boundary.  If that physical authority is malformed, surface the
        # exact validation failure to the caller; never pretend systemd can repair it
        # from inside the sealed Step where systemd-run is intentionally unavailable.
        resolve_certification_cgroup_root(environment)
        return False
    try:
        resolve_certification_cgroup_root(environment)
    except (FleetPlanInvalid, OSError):
        return True
    return False


def run_under_systemd_delegation(
    root: Path,
    *,
    environment: dict[str, str] | None = None,
    timing: RuntimeAcceptanceTiming | None = None,
) -> int:
    source_environment = os.environ if environment is None else environment
    resolved_timing = RuntimeAcceptanceTiming.resolve(source_environment) if timing is None else timing
    path = source_environment.get("PATH", "").strip()
    if not path:
        print("Local-access dev runtime acceptance failed:")
        print(
            "  - delegated re-entry requires the invoking PATH so repository-owned "
            "tools remain discoverable"
        )
        return 1
    systemd_run = shutil.which("systemd-run", path=path)
    if not systemd_run:
        print("Local-access dev runtime acceptance failed:")
        print("  - local strict executor needs delegated cgroup-v2 authority and systemd-run is unavailable")
        print(f"  - run `make strict-executor-setup` or configure {EXECUTORS_FILE_ENV}")
        return 1
    command = (
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        "--property=Type=exec", "--property=Delegate=yes",
        f"--setenv={LOCAL_RUNTIME_DELEGATED_ENV}=1",
        f"--setenv={SYSTEMD_DELEGATED_ENV}=1",
        f"--setenv={SYSTEMD_SUBGROUP_ENV}={LOCAL_RUNTIME_PROCESS_SUBGROUP}",
        f"--setenv=PATH={path}",
        *(
            (f"--setenv={EXECUTORS_FILE_ENV}={source_environment[EXECUTORS_FILE_ENV]}",)
            if source_environment.get(EXECUTORS_FILE_ENV, "").strip()
            else ()
        ),
        f"--setenv={RESOLVED_TIMING_ENV}={resolved_timing.serialized()}",
        sys.executable, "-m", "oh_goon.repository_tooling.dev_runtime_acceptance",
    )
    return subprocess.run(command, cwd=root, check=False).returncode
