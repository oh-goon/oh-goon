"""Explicit repair recapture preserves the previous immutable delivery."""

from __future__ import annotations

import errno
import os
from dataclasses import replace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.errors import OperationCancelledError
from tests.unit.services.test_ag02b_typed_agent_prework_handoff import _case


def _capture_case(tmp_path):
    resolver, plan, options, goon, repository = _case(
        tmp_path, step="- uses: agent\n  harness: local-search\n  task: search:source\n"
    )
    first = resolver.prepare_agent_step_handoff(plan, **options)
    options.pop("expected_source_sha256")
    return resolver, plan, options, goon, repository, first


def test_authorized_recapture_keeps_old_packet_and_selects_new_bytes(tmp_path, monkeypatch):
    resolver, plan, options, _, repository, first = _capture_case(tmp_path)
    packet = first.encode_prework_delivery()
    (repository / "secret.txt").write_bytes(b"repaired source\n")
    second = resolver.recapture_agent_step_handoff(
        plan,
        previous_handoff=first,
        expected_previous_handoff_identity=first.handoff_identity,
        **options,
    )
    assert first.encode_prework_delivery() == packet
    assert first.source.repository.members[0].data == b"private source\n"
    assert second.source.repository.members[0].data == b"repaired source\n"
    assert first.handoff_identity != second.handoff_identity

    # Resolving an explicitly selected handoff must not acquire another capture.
    (repository / "secret.txt").write_bytes(b"later unrelated change\n")
    monkeypatch.setattr(
        "oh_goon.services.agent_authoring_sources._snapshot_authorized_tree",
        lambda *args, **kwargs: pytest.fail("selected capture was read again"),
    )
    resolved, selected, _ = resolver.resolve_frozen_agent(plan, handoff=second, **options)
    assert selected.encode_prework_delivery() == second.encode_prework_delivery()
    assert resolved.steps[0].handoff_identity == second.handoff_identity


@pytest.mark.parametrize(
    "change",
    ["expected_identity", "plan", "goon", "repository", "skill", "acceptance", "previous_task"],
)
def test_changed_repair_authority_refused_before_source_read(tmp_path, monkeypatch, change):
    resolver, plan, options, goon, _, first = _capture_case(tmp_path)
    expected = first.handoff_identity
    if change == "expected_identity":
        expected = "agent-step-prework-handoff-v1-" + "0" * 64
    elif change == "plan":
        plan.write_text(plan.read_text() + "# changed Plan\n")
    elif change == "goon":
        source = goon / "goon.yml"
        source.write_text(source.read_text().replace("search:source", "search:other"))
    elif change == "previous_task":
        first = replace(first, task="search:unrelated")
        expected = first.handoff_identity
    else:
        key = {"repository": "repo", "skill": "skill", "acceptance": "acceptance"}[change]
        options["inputs"] = {
            **options["inputs"],
            key: "review-other" if key == "acceptance" else "local:other/source",
        }
    monkeypatch.setattr(
        "oh_goon.services.agent_authoring_sources._snapshot_authorized_tree",
        lambda *args, **kwargs: pytest.fail("invalid repair reached source acquisition"),
    )
    with pytest.raises(FleetPlanInvalid):
        resolver.recapture_agent_step_handoff(
            plan,
            previous_handoff=first,
            expected_previous_handoff_identity=expected,
            **options,
        )


def test_changed_skill_requires_new_authorization(tmp_path):
    resolver, plan, options, _, _, first = _capture_case(tmp_path)
    (options["skill_root"] / "team/check/SKILL.md").write_text("# Different tool instructions\n")
    with pytest.raises(FleetPlanInvalid, match="Skill"):
        resolver.recapture_agent_step_handoff(
            plan,
            previous_handoff=first,
            expected_previous_handoff_identity=first.handoff_identity,
            **options,
        )


@pytest.mark.parametrize("cancel_at", ["before", "during"])
def test_cancelled_recapture_never_returns_a_next_handoff(tmp_path, monkeypatch, cancel_at):
    resolver, plan, options, _, _, first = _capture_case(tmp_path)
    cancelled = cancel_at == "before"
    read_descriptor = None
    original_read = os.read

    def cancel_after_read(*args):
        nonlocal cancelled, read_descriptor
        data = original_read(*args)
        read_descriptor = args[0]
        cancelled = True
        return data

    if cancel_at == "during":
        monkeypatch.setattr(os, "read", cancel_after_read)
    with pytest.raises(OperationCancelledError):
        resolver.recapture_agent_step_handoff(
            plan,
            previous_handoff=first,
            expected_previous_handoff_identity=first.handoff_identity,
            cancel_requested=lambda: cancelled,
            **options,
        )
    if read_descriptor is not None:
        with pytest.raises(OSError) as closed:
            os.fstat(read_descriptor)
        assert closed.value.errno == errno.EBADF
    first.verify()


def test_selected_handoff_refuses_different_step_or_input_meaning(tmp_path):
    resolver, plan, options, _, _, first = _capture_case(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="meaning"):
        resolver.resolve_frozen_agent(
            plan, handoff=replace(first, task="search:another"), **options
        )
    options["inputs"] = {**options["inputs"], "repo": "local:other/source"}
    with pytest.raises(FleetPlanInvalid, match="selection"):
        resolver.resolve_frozen_agent(plan, handoff=first, **options)


def test_cancellation_after_capture_still_refuses_next_handoff(tmp_path, monkeypatch):
    from oh_goon.services import agent_authoring_sources

    resolver, plan, options, _, _, first = _capture_case(tmp_path)
    cancelled = False
    capture = agent_authoring_sources.capture_agent_sources

    def cancel_after_capture(*args, **kwargs):
        nonlocal cancelled
        source = capture(*args, **kwargs)
        cancelled = True
        return source

    monkeypatch.setattr(agent_authoring_sources, "capture_agent_sources", cancel_after_capture)
    with pytest.raises(OperationCancelledError):
        resolver.recapture_agent_step_handoff(
            plan,
            previous_handoff=first,
            expected_previous_handoff_identity=first.handoff_identity,
            cancel_requested=lambda: cancelled,
            **options,
        )
