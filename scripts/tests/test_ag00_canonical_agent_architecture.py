"""AG-00: adversarial first-slice architecture protection; no Agent Step effects."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_agent_workload_canonical_owner,
    check_x0_single_execution_path,
)


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    """Clone only the frozen source-owner surfaces and accepted ADR, not live state."""
    for relative in (
        "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md",
        "backend/pyproject.toml",
        "backend/src/oh_goon/services/fleet_run_execution.py",
        "backend/src/oh_goon/services/fleet_run_control_plane.py",
        "backend/src/oh_goon/integrations/fleet_plan_run_execution.py",
    ):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    assert (
        _check_agent_workload_canonical_owner(
            tmp_path, tmp_path / "backend/src/oh_goon"
        )
        == []
    )
    return tmp_path


def _check(root: Path) -> list[str]:
    return [
        issue.message
        for issue in _check_agent_workload_canonical_owner(
            root, root / "backend/src/oh_goon"
        )
    ]


def test_full_existing_architecture_gate_integrates_agent_census() -> None:
    assert check_x0_single_execution_path() == []


@pytest.mark.parametrize(
    "klass",
    [
        "AgentRunExecutor",
        "AgentStepScheduler",
        "AgentEnforcer",
        "AgentJournal",
        "AgentPhysicalObserver",
        "AgentLoopRunner",
        "AgentRecovery",
        "AgentSandboxManager",
        "AgentTerminalClassifier",
        "AgentPolicyEngine",
        "AgentOrchestrator",
    ],
)
def test_separate_agent_authority_classes_fail_before_work(
    baseline: Path, klass: str
) -> None:
    candidate = baseline / "backend/src/oh_goon/services/agent_host.py"
    candidate.write_text(f"class {klass}:\n    pass\n", encoding="utf-8")
    assert any("agent-specific authority owner" in m for m in _check(baseline))


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\ndef call():\n    subprocess.run(["codex"])\n',
        'import os\ndef call():\n    os.execve("/usr/bin/codex", [], {})\n',
        'import asyncio\nasync def call():\n    await asyncio.create_subprocess_exec("codex")\n',
        'import sqlite3\ndef call():\n    sqlite3.connect("/tmp/agent-store")\n',
        'import subprocess as sp\ndef call():\n    sp.Popen(["codex"])\n',
        'from subprocess import run as exec_agent\ndef call():\n    exec_agent(["codex"])\n',
        "def call(original_journal):\n    original_journal.publish_local_resource_intent()\n",
    ],
)
def test_adapter_direct_effect_bypass_rejected(baseline: Path, source: str) -> None:
    candidate = baseline / "backend/src/oh_goon/integrations/agent_harness_adapter.py"
    candidate.write_text(source, encoding="utf-8")
    assert any("agent adapter bypass effect" in m for m in _check(baseline))


def test_pure_adapter_does_not_create_an_alternate_owner(baseline: Path) -> None:
    candidate = baseline / "backend/src/oh_goon/integrations/agent_harness_adapter.py"
    candidate.write_text(
        "def describe_admitted_workload(request):\n    return request.argv\n",
        encoding="utf-8",
    )
    assert _check(baseline) == []


def test_agent_cli_side_entrance_rejected(baseline: Path) -> None:
    pyproject = baseline / "backend/pyproject.toml"
    pyproject.write_text(
        pyproject.read_text()
        + '\n"oh-goon-agent" = "oh_goon.services.agent_host:main"\n'
    )
    # Inject under the actual scripts table, not under another TOML owner.
    text = pyproject.read_text()
    text = text.replace(
        "[project.scripts]",
        '[project.scripts]\n"oh-goon-agent-launch" = "oh_goon.services.agent_host:main"',
        1,
    )
    pyproject.write_text(
        text.replace('\n"oh-goon-agent" = "oh_goon.services.agent_host:main"\n', "\n")
    )
    assert any("separate agent CLI authority" in m for m in _check(baseline))


@pytest.mark.parametrize(
    ("path", "owner", "method", "old", "replacement"),
    [
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "_run",
            "self._targets.execute(",
            "self._agent_targets.execute(",
        ),
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "execute",
            "self._lifecycle.finish_detail(",
            "self._agent_journal.finish_detail(",
        ),
        (
            "integrations/fleet_plan_run_execution.py",
            "FleetPlanRunExecutor",
            "execute_strict_run",
            "sequence.run_strict(",
            "sequence.agent_run_strict(",
        ),
        (
            "services/fleet_run_control_plane.py",
            "FleetRunControlPlane",
            "start_bound",
            "return self._start_intent(",
            "return self._start_agent_intent(",
        ),
        (
            "services/fleet_run_execution.py",
            "FleetRunExecution",
            "resume_local_lace_takeover",
            "coordinator.resume(",
            "coordinator.agent_resume(",
        ),
    ],
)
def test_canonical_call_edge_mutant_cannot_silently_fork(
    baseline: Path, path: str, owner: str, method: str, old: str, replacement: str
) -> None:
    file = baseline / "backend/src/oh_goon" / path
    source = file.read_text(encoding="utf-8")
    assert old in source
    start = source.index(f"    def {method}(")
    end = source.find("\n    def ", start + 1)
    if end < 0:
        end = len(source)
    section = source[start:end]
    assert old in section
    file.write_text(
        source[:start] + section.replace(old, replacement, 1) + source[end:],
        encoding="utf-8",
    )
    assert any("canonical call edge" in m for m in _check(baseline))


def test_accepted_adr_cannot_be_renamed_to_weaken_security(baseline: Path) -> None:
    adr = baseline / "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md"
    adr.write_text(
        adr.read_text().replace(
            "We will not build an agent runtime", "We may build an agent runtime", 1
        )
    )
    assert any(
        "accepted agent canonical-execution ADR changed" in m for m in _check(baseline)
    )


def test_agent_adr_missing_after_enrollment_fails_closed(baseline: Path) -> None:
    census = baseline / "docs/architecture/AG00_CANONICAL_AUTHORITY_CENSUS.md"
    census.write_text("enrolled", encoding="utf-8")
    (baseline / "docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md").unlink()
    assert any("ADR missing from enrolled source" in m for m in _check(baseline))


def test_hosted_command_step_both_modes_delegate_one_canonical_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hosted method spy, NOT a native signed-Run or Bubblewrap trace."""
    from types import SimpleNamespace
    from typing import cast

    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
    from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
    from oh_goon.integrations import fleet_plan_run_execution as module
    from oh_goon.integrations.fleet_plan_run import PreparedPlanRun

    visits: list[tuple[str, object]] = []
    marker = object()

    class Sequence:
        def __init__(self, repository: Path, runtime: object, observer: object) -> None:
            assert repository == tmp_path
            assert runtime is marker
            visits.append(("original_sequence", runtime))

        def run_direct(self, step: object) -> None:
            visits.append(("original_direct", step))

        def run_strict(self, prepared: object, step: object) -> None:
            visits.append(("original_strict", step))

    monkeypatch.setattr(module, "FleetPlanStepSequence", Sequence)
    monkeypatch.setattr(
        module.FleetPlanRunExecutor, "_step_runtime", lambda *a, **k: marker
    )
    monkeypatch.setattr(
        module.FleetPlanRunExecutor,
        "_outcome",
        staticmethod(
            lambda sequence, *, execution_inputs=None: (
                "canonical_outcome",
                execution_inputs,
            )
        ),
    )
    executor = object.__new__(module.FleetPlanRunExecutor)
    step = object()
    direct = SimpleNamespace(
        execution=SimpleNamespace(purpose=ExecutionPurpose.MAINTENANCE), steps=(step,)
    )
    strict = SimpleNamespace(
        execution=SimpleNamespace(purpose=ExecutionPurpose.CICD),
        steps=(step,),
        plan_id="original-plan",
    )
    evidence = object()
    # Preserve the existing strict execution object's explicit optional TL-1
    # fields in this hosted spy. A missing attribute is not a realistic
    # PreparedPlanRun and must not mask canonical sequence ownership.
    prepared = SimpleNamespace(
        plan_id="original-plan",
        trusted_local_consent=None,
        trusted_local_scope=None,
        bind_execution_cancel_requested=lambda f: None,
        execution_input_evidence=lambda: evidence,
    )

    direct_outcome: object = executor.execute_direct_run(
        tmp_path, cast(ResolvedFleetPlan, direct), cancel_requested=None
    )
    assert direct_outcome == (
        "canonical_outcome",
        None,
    )
    strict_outcome: object = executor.execute_strict_run(
        tmp_path,
        cast(ResolvedFleetPlan, strict),
        cast(PreparedPlanRun, prepared),
        cancel_requested=None,
    )
    assert strict_outcome == (
        "canonical_outcome",
        evidence,
    )
    assert visits == [
        ("original_sequence", marker),
        ("original_direct", step),
        ("original_sequence", marker),
        ("original_strict", step),
    ]
