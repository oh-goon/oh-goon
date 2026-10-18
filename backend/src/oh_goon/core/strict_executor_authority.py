from __future__ import annotations

"""Canonical process-environment vocabulary for strict executor authority.

These variables carry release/CI or explicit strict-acceptance placement authority.
APP/self verification does not consume them and must strip them before orchestration.
"""

from collections.abc import MutableMapping


EXECUTORS_FILE_ENV = "OH_GOON_EXECUTORS_FILE"
CERTIFICATION_CGROUP_ROOT_ENV = "OH_GOON_CERTIFICATION_CGROUP_ROOT"
SYSTEMD_DELEGATED_ENV = "OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED"
SYSTEMD_SUBGROUP_ENV = "OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP"

STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT = (
    EXECUTORS_FILE_ENV,
    CERTIFICATION_CGROUP_ROOT_ENV,
    SYSTEMD_DELEGATED_ENV,
    SYSTEMD_SUBGROUP_ENV,
)


def strip_strict_executor_authority(environment: MutableMapping[str, str]) -> None:
    """Remove strict placement authority from a process environment in-place."""

    for name in STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT:
        environment.pop(name, None)
