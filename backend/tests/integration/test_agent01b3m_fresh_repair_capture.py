"""Authorized repair attempts deliver distinct captures through original Fleet."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import threading
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.test_agent01b2b_original_fleet import _case, _lock
from tests.integration.test_agent01b3i_source_bound_patch_proposal import _case as proposal_case
from tests.integration.test_agent01b3i_source_bound_patch_proposal import _payload


def _source_options(options):
    return dict(
        inputs=options["inputs"],
        repository_root=options["agent_repository_root"],
        skill_root=options["agent_skill_root"],
        allowed_acceptance=options["allowed_agent_acceptance"],
    )


def _recapture(plan, options, previous):
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    handoff = resolver.recapture_agent_step_handoff(
        plan,
        previous_handoff=previous,
        expected_previous_handoff_identity=previous.handoff_identity,
        **_source_options(options),
    )
    frozen, selected, _ = resolver.resolve_frozen_local_search_agent(
        plan, handoff=handoff, **_source_options(options)
    )
    assert selected == handoff
    write_execution_lock(plan.parent / "goon.lock", build_execution_lock(frozen))
    return handoff


def test_fresh_capture_delivered_after_real_apply_and_failed_retest(tmp_path, monkeypatch):
    revised = "revised content proposed, not applied\n"
    repaired = "final repaired content\n"
    plan, repository, options = proposal_case(
        tmp_path,
        monkeypatch,
        model_replies=[
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(),
            '{"tool":"read_file","path":"repo/code.txt"}',
            _payload(
                source_sha256=hashlib.sha256(revised.encode()).hexdigest(),
                replacement=repaired,
            ),
        ],
    )
    goon = tmp_path / "goons/agent/search/goon.yml"
    project = shlex.quote(str(Path(__file__).resolve().parents[2]))
    with goon.open("a") as out:
        out.write(
            """  - id: apply
    run: "uv run --project PROJECT --offline --frozen --no-sync oh-goon agent apply --proposal ${{ steps.agent.outputs.proposal }} --expected-path code.txt"
  - id: test
    run: "rg --fixed-strings --line-regexp 'final repaired content' code.txt"
""".replace("PROJECT", project)
        )
    _, first = _lock(plan, options)
    old_packet = first.encode_prework_delivery()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        old_run = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="b3m-before",
            agent_handoff=first,
            **options,
        )
        failed = runtime.runs.wait_for_terminal(old_run.fleet_run_id, timeout=30)
        assert [
            (step.status, getattr(step, "returncode", None))
            for step in failed.operations[0].evidence.plan.steps
        ] == [("COMPLETED", 0), ("COMPLETED", 0), ("FAILED", 1)], [
            chunk.text for chunk in runtime.runs.game_tape_live_output(old_run.fleet_run_id).chunks
        ]
        assert failed.game_tape.status.value != "PASSED"
        assert (repository / "code.txt").read_text() == revised

        second = _recapture(plan, options, first)
        assert first.encode_prework_delivery() == old_packet
        assert second.handoff_identity != first.handoff_identity
        assert second.source.repository.members[0].data == revised.encode()
        monkeypatch.setattr(
            "oh_goon.services.agent_authoring_sources._snapshot_authorized_tree",
            lambda *args, **kwargs: pytest.fail("admission recaptured selected source"),
        )
        new_run = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="b3m-after",
            agent_handoff=second,
            **options,
        )
        completed = runtime.runs.wait_for_terminal(new_run.fleet_run_id, timeout=30)
        assert [step.returncode for step in completed.operations[0].evidence.plan.steps] == [
            0,
            0,
            0,
        ]
        assert completed.game_tape.status.value == "PASSED"
        assert (repository / "code.txt").read_text() == repaired
        assert revised.strip() in (tmp_path / "prompt-4.txt").read_text()
        assert runtime.runs.get("b3m-before").operations == failed.operations
        old_reference = old_run.target.agent_deliveries[0]
        new_reference = new_run.target.agent_deliveries[0]
        assert old_reference.delivery_sha256 == hashlib.sha256(old_packet).hexdigest()
        assert (
            new_reference.delivery_sha256
            == hashlib.sha256(second.encode_prework_delivery()).hexdigest()
        )
    finally:
        runtime.shutdown(wait_for_runs=True)

    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        before = reopened.runs.get("b3m-before")
        after = reopened.runs.get("b3m-after")
        assert before.operations[0].evidence.plan.steps[2].returncode == 1
        assert before.game_tape.status.value != "PASSED"
        assert after.game_tape.status.value == "PASSED"
        assert before.run.target.agent_deliveries == (old_reference,)
        assert after.run.target.agent_deliveries == (new_reference,)
        retained = FleetContentStore(
            ApplicationStatePathLayout.from_database(tmp_path / "database.sqlite").content_store
        )
        for reference, packet in (
            (old_reference, old_packet),
            (new_reference, second.encode_prework_delivery()),
        ):
            stored = retained.get(
                reference.reference_id,
                owner_repository_id=repository_id(repository),
                expected_kind="file",
            )
            assert stored.path.read_bytes() == packet
    finally:
        reopened.shutdown(wait_for_runs=True)


def test_stale_selected_capture_refused_before_native_effect(tmp_path, monkeypatch):
    plan, repository, options = _case(tmp_path, monkeypatch)
    _, first = _lock(plan, options)
    (repository / "code.txt").write_text("needle after authorized repair\n")
    _recapture(plan, options, first)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        with pytest.raises(FleetPlanInvalid, match="stale"):
            runtime.plans.run(
                plan,
                repository_ids={repository_id(repository)},
                fleet_run_id="b3m-stale",
                agent_handoff=first,
                **options,
            )
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_selected_capture_cancelled_before_subsequent_native_launch(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as steps

    plan, repository, options = _case(tmp_path, monkeypatch)
    _, first = _lock(plan, options)
    (repository / "code.txt").write_text("needle after authorized repair\n")
    second = _recapture(plan, options, first)
    native_rg = shutil.which("rg")
    assert native_rg is not None
    binary = tmp_path / "bin/rg"
    binary.parent.mkdir()
    marker = tmp_path / "tool-started"
    binary.write_text(
        "#!/bin/sh\n"
        + 'if [ "$1" = --no-heading ]; then printf started > '
        + shlex.quote(str(marker))
        + "; fi\n"
        + "exec "
        + shlex.quote(native_rg)
        + ' "$@"\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + ":" + os.environ["PATH"])
    entered, release = threading.Event(), threading.Event()
    original = steps._execute_command_step

    def pause_before_launch(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=15), "native launch handshake timed out"
        return original(*args, **kwargs)

    monkeypatch.setattr(steps, "_execute_command_step", pause_before_launch)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent.parent)
        started = runtime.plans.run(
            plan,
            repository_ids={repository_id(repository)},
            fleet_run_id="b3m-cancel",
            agent_handoff=second,
            **options,
        )
        assert entered.wait(timeout=15), "selected capture never reached original supervisor"
        runtime.runs.cancel(started.fleet_run_id)
        release.set()
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert detail.run.status.value == "CANCELLED"
        assert runtime.runs.get(started.fleet_run_id).run.status.value == "CANCELLED"
        assert not marker.exists()
    finally:
        release.set()
        runtime.shutdown(wait_for_runs=True)
