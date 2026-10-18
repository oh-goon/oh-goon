"""M186: malformed certification ingress must fail before source/work authority."""

from __future__ import annotations

import pytest

from oh_goon.services import certification_frontdoor as frontdoor
from oh_goon.workflow import workflow_intent


@pytest.mark.parametrize(
    "args",
    [
        ["bogus"],
        ["release", "--unexpected"],
        ["app", "release"],
        ["verify", "--wait"],
        ["explain"],
        ["explain", "  "],
        ["explain", "run-id", "unexpected"],
    ],
)
def test_invalid_certification_ingress_rejects_before_source_or_work(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """An unsupported command cannot touch static admission, state, or Fleet work."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("malformed command crossed certification prework boundary")

    monkeypatch.setattr(frontdoor, "_static_source_admission", forbidden)
    monkeypatch.setattr(frontdoor, "_certification_process_environment", forbidden)
    monkeypatch.setattr(frontdoor, "_workflow_main_with_state_lease", forbidden)
    monkeypatch.setattr(frontdoor, "_enter_systemd_delegation", forbidden)
    assert frontdoor.main(args) == 2


@pytest.mark.parametrize(
    ("args", "intent"),
    [
        ([], "app"),
        (["app"], "app"),
        (["verify"], "verify"),
        (["release"], "release"),
        (["explain", "run-id"], "explain"),
    ],
)
def test_workflow_command_intent_is_the_single_parser_owner(
    args: list[str], intent: str
) -> None:
    assert workflow_intent(args) == intent


def test_certification_valid_command_still_enters_original_workflow(monkeypatch) -> None:
    observed: list[list[str]] = []
    monkeypatch.setattr(frontdoor, "_static_source_admission", lambda: True)
    monkeypatch.setattr(
        frontdoor,
        "_workflow_main_with_state_lease",
        lambda args: observed.append(list(args)) or 0,
    )
    assert frontdoor.main(["app"]) == 0
    assert observed == [["app"]]