from __future__ import annotations

import json
from dataclasses import asdict

from oh_goon.repository_tooling.scout_http_load_probe import run_http_probe


def main() -> int:
    result = run_http_probe(enforce_loopback_only=True)
    if result.blocked_outbound_attempts != 0:
        raise RuntimeError("Scout network certification observed blocked outbound attempts")
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
