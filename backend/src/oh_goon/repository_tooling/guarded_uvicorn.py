from __future__ import annotations

import argparse
import os
from pathlib import Path

from oh_goon.repository_tooling.runtime_loopback_guard import LoopbackNetworkGuard


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Oh Goon Uvicorn with loopback-only outbound certification"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="warning")
    return parser


def main() -> int:
    args = _parser().parse_args()
    audit = os.environ.get("OH_GOON_LOOPBACK_GUARD_LOG")
    if not audit:
        raise SystemExit("OH_GOON_LOOPBACK_GUARD_LOG is required")
    LoopbackNetworkGuard(Path(audit)).install()

    import uvicorn

    uvicorn.run(
        "oh_goon.api.app:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
