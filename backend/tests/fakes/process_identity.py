"""Portable test-process identity for runtimes without a mounted procfs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def stable_test_process_identity_without_procfs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if Path(f"/proc/{os.getpid()}/stat").is_file():
        return

    identity = f"test-process:{os.getpid()}"

    def process_identity(pid: int) -> str | None:
        return identity if pid == os.getpid() else None

    monkeypatch.setattr(
        "oh_goon.runtime._executor_placement_queue._process_identity",
        process_identity,
    )
    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_restricted_egress._process_identity",
        process_identity,
    )
    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_cgroup_resources._process_identity",
        process_identity,
    )
