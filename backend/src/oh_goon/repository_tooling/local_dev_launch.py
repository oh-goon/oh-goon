from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from oh_goon.api.local_access import new_local_access_token
from oh_goon.cli_local_state import check_local_state_for_startup
from oh_goon.runtime.local_launch_plan import LocalLaunchPlan, contributor_dev_plan
from oh_goon.runtime.local_launch_supervisor import (
    LocalLaunchStartupError,
    RunningLocalLaunch,
    start_contributor_launch,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contributor local-runtime frontdoor")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    return run(Path(args.workspace))


def _wait_for_required_service_exit(running: RunningLocalLaunch, plan: LocalLaunchPlan) -> int:
    while True:
        exited = None
        for service, process in zip(plan.services, running.processes, strict=True):
            code = process.poll()
            if code is not None:
                exited = (service, code)
                break
        if exited is not None:
            service, code = exited
            assert code is not None
            running.owner.service_failed(
                service.service,
                service.generation,
                code="child_exit",
                reason=f"{service.service} process exited with status {code}",
                retryable=True,
            )
            running.owner.launch_failed(
                code="required_service_exit",
                reason=f"required local service {service.service} exited with status {code}",
                retryable=True,
            )
            return 1
        time.sleep(0.2)


def run(workspace: Path) -> int:
    # Repository/state preflight is deliberately outside startup timing authority.
    if check_local_state_for_startup() != 0:
        return 2
    plan = contributor_dev_plan(workspace)
    try:
        running = start_contributor_launch(
            root=workspace, env=dict(os.environ), plan=plan, token=new_local_access_token()
        )
    except LocalLaunchStartupError as exc:
        print(f"OH-GOON DEV STARTUP FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Oh Goon dev access: http://127.0.0.1:5173/#access={running.token}")
    print(f"Launch: {running.owner.identity.launch_id}")
    try:
        return _wait_for_required_service_exit(running, plan)
    except KeyboardInterrupt:
        return 130
    finally:
        running.stop()


if __name__ == "__main__":
    raise SystemExit(main())
