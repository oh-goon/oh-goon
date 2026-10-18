from __future__ import annotations

import argparse
import os
from pathlib import Path

from oh_goon.runtime.local_launch_owner import LocalLaunchOwner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record an owned child PID, then exec its native runtime"
    )
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--asserted-by", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("--spawn-ready-fd", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("native command is required after --")

    if args.cwd:
        os.chdir(Path(args.cwd))
    owner = LocalLaunchOwner.load(args.launch_id, asserted_by=args.asserted_by)
    owner.spawned(args.service, args.generation, os.getpid())
    if args.spawn_ready_fd is not None:
        os.write(args.spawn_ready_fd, b"1")
        os.close(args.spawn_ready_fd)
    os.execvp(command[0], command)
    raise AssertionError("os.execvp returned unexpectedly")


if __name__ == "__main__":
    raise SystemExit(main())
