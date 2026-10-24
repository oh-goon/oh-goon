"""M198: verify the native Fleet issuer from an independently installed wheel.

This is a post-install observation, never admission, provisioning, signing or a
substitute for the WSL certification ladder. Run it in an isolated interpreter.
"""

from __future__ import annotations

import argparse
import json
from importlib import import_module, metadata
from pathlib import Path

ISSUER_MODULES = (
    "enforcer_lace_backend_capability_issuer",
    "enforcer_lace_captured_run_issuer",
    "enforcer_lace_execution_environment_issuer",
    "enforcer_lace_native_issuer_suite",
    "enforcer_lace_native_lock_issuer",
    "enforcer_lace_native_tool_issuer",
    "enforcer_lace_prework_capture",
    "enforcer_lace_repository_snapshot_issuer",
    "enforcer_lace_run_source_issuer",
    "enforcer_lace_signer_custody",
)


class WheelAuthorityFailure(RuntimeError):
    """An installed wheel cannot prove its canonical issuer ownership."""


def prove_wheel_owner(
    *,
    distribution: metadata.Distribution,
    expected_version: str,
) -> dict[str, object]:
    """Require installed RECORD membership, exact origin, and one alias module object."""
    if not expected_version or distribution.version != expected_version:
        raise WheelAuthorityFailure("installed version differs from requested source version")
    members = distribution.files
    if not members:
        raise WheelAuthorityFailure("installed wheel has no member RECORD")
    filenames = {str(part) for part in members}
    if "oh_goon/native_issuer/__init__.py" not in filenames:
        raise WheelAuthorityFailure("native issuer installed package is missing")
    result = []
    for name in ISSUER_MODULES:
        relative = f"oh_goon/native_issuer/{name}.py"
        if relative not in filenames:
            raise WheelAuthorityFailure(f"issuer owner missing from installed wheel: {name}")
        path = Path(str(distribution.locate_file(relative))).resolve(strict=True)
        owner = import_module(f"oh_goon.native_issuer.{name}")
        compatibility = import_module(f"oh_goon.repository_tooling.{name}")
        actual = Path(getattr(owner, "__file__", "")).resolve()
        if actual != path or compatibility is not owner:
            raise WheelAuthorityFailure(f"issuer import identity or wheel origin differs: {name}")
        result.append(name)
    return {
        "schema": "oh-goon.native-installed-issuer-wheel/v1",
        "source_version": expected_version,
        "issuer_count": len(result),
        "issuer_modules": result,
        "installed_wheel_import_identity": "PASS",
        "authenticated_replay_authority": "NOT_ESTABLISHED",
        "fleet_enforcer_provisioning": "NOT_ESTABLISHED",
        "group_a_gate_a": "NOT_RUN",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify installed native issuer wheel identity")
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)
    try:
        result = prove_wheel_owner(
            distribution=metadata.distribution("oh-goon"),
            expected_version=args.expected_version,
        )
    except (metadata.PackageNotFoundError, OSError, ImportError, WheelAuthorityFailure) as exc:
        parser.exit(2, f"NATIVE INSTALLED ISSUER WHEEL BLOCKED: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
