from __future__ import annotations

import argparse

from oh_goon.api.local_access import new_local_access_token
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch-id")
    parser.add_argument("--owner-generation", type=int)
    args = parser.parse_args(argv)
    if (args.launch_id is None) != (args.owner_generation is None):
        parser.error("--launch-id and --owner-generation must be supplied together")
    token = new_local_access_token()
    if args.launch_id is not None:
        LocalLaunchAccessStore.configured().publish(
            launch_id=args.launch_id,
            owner_generation=args.owner_generation,
            token=token,
        )
    print(token)
    return 0
