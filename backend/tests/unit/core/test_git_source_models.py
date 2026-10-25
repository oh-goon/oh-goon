"""Explicit source selections and frozen pins reject ambiguous authority."""

import pytest
from pydantic import ValidationError

from oh_goon.core.models.execution_lock_models import ExecutionLock
from oh_goon.core.models.git_sources import GoonGitSource, LockedGoonSource
from oh_goon.core.models.run_configuration import GitPlanSelection


def _source(**overrides):
    return {
        "git": "https://example.test/workflows.git",
        "ref": "main",
        "path": "goons/general/check",
        **overrides,
    }


def _lock(sources):
    return ExecutionLock(
        lock_version=1, plan="sha256:" + "a" * 64, digest="sha256:" + "b" * 64, goon_sources=sources
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"git": "https:///missing-host.git"},
        {"git": "https://user:secret@example.test/repository.git"},
        {"git": "ssh://git:secret@example.test/repository.git"},
        {"git": "git:secret@example.test:repository.git"},
        {"git": "https://example.test/repository.git?secret=value"},
        {"git": "file://foreign-host/repository.git"},
        {"git": "ext::command"},
        {"ref": "main:other"},
        {"path": "../general/check"},
        {"path": "goons/general/line\nbreak"},
    ],
)
def test_source_coordinates_refuse_invalid_boundary(overrides):
    assert GoonGitSource(**_source()).path == "goons/general/check"
    with pytest.raises(ValidationError):
        GoonGitSource(**_source(**overrides))


@pytest.mark.parametrize("plan", ["goon.yml", "nested/goon.yml", ".", "../plan.yml"])
def test_git_plan_rejects_non_plan_selection_before_acquisition(plan):
    assert (
        GitPlanSelection(kind="git-plan", **_source(path="."), plan="plan.yml").plan == "plan.yml"
    )
    with pytest.raises(ValidationError):
        GitPlanSelection(kind="git-plan", **_source(path="."), plan=plan)


@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "goon", "plan": "plan.yml"},
        {"kind": "plan"},
        {"kind": "plan", "plan": "../plan.yml"},
        {"kind": "plan", "plan": "goon.yml"},
    ],
)
def test_locked_source_kind_and_plan_are_consistent(selection):
    assert LockedGoonSource(**_source(), commit="a" * 40).kind == "goon"
    with pytest.raises(ValidationError):
        LockedGoonSource(**_source(), commit="a" * 40, **selection)


def test_lock_refuses_duplicate_or_conflicting_selected_source_pins():
    first = LockedGoonSource(**_source(), commit="a" * 40)
    second = LockedGoonSource(**_source(path="goons/general/second"), commit="a" * 40)
    assert len(_lock((first, second)).goon_sources) == 2
    with pytest.raises(ValidationError, match="duplicate"):
        _lock((first, first))
    with pytest.raises(ValidationError, match="disagree"):
        _lock((first, second.model_copy(update={"commit": "b" * 40})))
    other_ref = second.model_copy(update={"ref": "v1", "commit": "b" * 40})
    assert len(_lock((first, other_ref)).goon_sources) == 2
