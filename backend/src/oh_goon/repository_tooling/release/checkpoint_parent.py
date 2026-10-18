from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon.repository_tooling.self_certification_artifacts import (
    PARENT_METADATA,
    _archive_version,
    _atomic_write,
    _sha256_file,
    parent_checkpoint_spec,
)


def bind_checkpoint_parent(root: Path, candidate: Path) -> dict[str, object]:
    root = root.resolve()
    candidate = candidate.expanduser().resolve()
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("checkpoint parent must be a regular non-symlink source ZIP")
    version = _archive_version(candidate)
    digest = _sha256_file(candidate)
    payload: dict[str, object] = {
        "schema": 1,
        "version": version,
        "artifact": f"oh-goon-{version}.zip",
        "sha256": digest,
    }
    destination = root / PARENT_METADATA
    _atomic_write(
        destination,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    observed = parent_checkpoint_spec(root)
    if observed != payload:
        raise RuntimeError("checkpoint parent declaration did not round-trip after binding")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bind/check the exact source ZIP declared as the self-certification checkpoint parent"
    )
    parser.add_argument("mode", choices=("bind", "check"))
    parser.add_argument("candidate", nargs="?")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[5]
    if args.mode == "bind":
        if args.candidate is None:
            parser.error("bind requires a source ZIP path")
        payload = bind_checkpoint_parent(root, Path(args.candidate))
    else:
        if args.candidate is not None:
            parser.error("check does not accept a source ZIP path")
        payload = parent_checkpoint_spec(root)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
