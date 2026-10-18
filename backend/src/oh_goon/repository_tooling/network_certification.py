from __future__ import annotations

import json
from pathlib import Path


def blocked_network_attempts(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    attempts: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError(
                "loopback network certification audit row must be a JSON object"
            )
        attempts.append(payload)
    return tuple(attempts)
