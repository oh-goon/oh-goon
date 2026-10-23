"""Native private source rejection precedes original Fleet Run admission."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane


@pytest.mark.parametrize("private_source", ["repository_config", "skill_inside_native_home"])
def test_codex_service_private_source_never_opens_or_creates_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private_source: str
) -> None:
    repository = tmp_path / "repositories" / "app"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("SOURCE")
    native_home = tmp_path / "native-owned"
    native_home.mkdir()
    skill: Path | None = None
    if private_source == "repository_config":
        sentinel = repository / ".codex" / "config.toml"
        sentinel.parent.mkdir()
    else:
        skill = native_home / "private-skill"
        skill.mkdir()
        sentinel = skill / "SKILL.md"
    sentinel.write_text("SYNTHETIC_PRIVATE_NATIVE_STATE")
    program = tmp_path / "public-runtime" / "codex"
    program.parent.mkdir()
    native_effect = tmp_path / "unexpected-native-start"
    program.write_text(f"#!/bin/sh\ntouch '{native_effect}'\nexit 99\n")
    program.chmod(0o700)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(native_home), model="native-model", reasoning_effort="high"
            )
        ),
        executable_sha256=native_executable_sha256(program),
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    runtime = ApplicationRuntime.build(standard_local=True)
    original_open = os.open
    attempted_private: list[object] = []

    def guarded_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not flags & os.O_DIRECTORY and Path(path).name == sentinel.name:
            attempted_private.append(path)
            pytest.fail("Private native source contents reached capture")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    try:
        with pytest.raises(FleetPlanInvalid, match="native authentication/configuration state"):
            runtime.plans.run_native_agent(
                selected=selected, workspace=repository, skill=skill, task="Never run this task"
            )
        assert runtime.runs.history().items == ()
        assert not native_effect.exists()
        assert attempted_private == []
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_original_native_source_preflight_repeats_with_empty_native_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repositories" / "app"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("SOURCE")
    native_home = tmp_path / "native-owned"
    native_home.mkdir()
    program = tmp_path / "public-runtime" / "codex"
    program.parent.mkdir()
    native_effect = tmp_path / "unexpected-native-start"
    program.write_text(f"#!/bin/sh\ntouch '{native_effect}'\nexit 99\n")
    program.chmod(0o700)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="owned-service",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(native_home), model="native-model", reasoning_effort="high"
            )
        ),
        executable_sha256=native_executable_sha256(program),
    )
    captured: list[object] = []
    original_resolve = FleetPlanResolver.resolve_frozen_agents

    def observe_source(self: FleetPlanResolver, *args: Any, **kwargs: Any) -> Any:
        result = original_resolve(self, *args, **kwargs)
        captured.append(tuple(handoff.source.repository for handoff in result[1].values()))
        return result

    class SourcePreflightComplete(Exception):
        pass

    def stop_before_admission(*_args: Any, **_kwargs: Any) -> Any:
        raise SourcePreflightComplete

    monkeypatch.setattr(FleetPlanResolver, "resolve_frozen_agents", observe_source)
    monkeypatch.setattr(FleetRunControlPlane, "start_prepared_plan", stop_before_admission)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        for attempt in range(2):
            if attempt:
                (repository / ".codex").mkdir()
                (repository / "nested" / ".opencode").mkdir(parents=True)
            with pytest.raises(SourcePreflightComplete):
                runtime.plans.run_native_agent(
                    selected=selected, workspace=repository, skill=None, task="Inspect source"
                )
        assert len(captured) == 2 and captured[0] == captured[1]
        assert runtime.runs.history().items == ()
        assert not native_effect.exists()
        assert list((repository / ".codex").iterdir()) == []
        assert list((repository / "nested" / ".opencode").iterdir()) == []
    finally:
        runtime.shutdown(wait_for_runs=True)
