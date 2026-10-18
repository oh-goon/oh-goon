from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from oh_goon.core.models.base import ImmutableModel


class GitPlanOperation(StrEnum):
    ASSERT_CLEAN = "assert-clean"
    CLEAN_IGNORED = "clean-ignored"
    DIFF = "diff"
    CHECKOUT = "checkout"
    CREATE_BRANCH = "create-branch"
    ADD = "add"
    COMMIT = "commit"
    NATIVE = "native"


class GitAssertCleanAction(ImmutableModel):
    operation: Literal[GitPlanOperation.ASSERT_CLEAN] = GitPlanOperation.ASSERT_CLEAN


class GitCleanIgnoredAction(ImmutableModel):
    operation: Literal[GitPlanOperation.CLEAN_IGNORED] = GitPlanOperation.CLEAN_IGNORED


class GitDiffAction(ImmutableModel):
    operation: Literal[GitPlanOperation.DIFF] = GitPlanOperation.DIFF


class GitCheckoutAction(ImmutableModel):
    operation: Literal[GitPlanOperation.CHECKOUT] = GitPlanOperation.CHECKOUT
    ref: str = Field(min_length=1, max_length=240)


class GitCreateBranchAction(ImmutableModel):
    operation: Literal[GitPlanOperation.CREATE_BRANCH] = GitPlanOperation.CREATE_BRANCH
    branch: str = Field(min_length=1, max_length=240)
    start_point: str | None = Field(default=None, min_length=1, max_length=240)


class GitAddAction(ImmutableModel):
    operation: Literal[GitPlanOperation.ADD] = GitPlanOperation.ADD
    paths: tuple[str, ...] = Field(min_length=1, max_length=64)


class GitCommitAction(ImmutableModel):
    operation: Literal[GitPlanOperation.COMMIT] = GitPlanOperation.COMMIT
    message: str = Field(min_length=1, max_length=500)
    allow_empty: bool = False


class GitNativeAction(ImmutableModel):
    """Escape hatch for native Git argv not modeled by Fleet built-ins."""

    operation: Literal[GitPlanOperation.NATIVE] = GitPlanOperation.NATIVE
    args: tuple[str, ...] = Field(min_length=1)

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in item for item in value):
            raise ValueError("Native Git arguments cannot contain NUL bytes")
        return value


GitPlanAction = Annotated[
    GitAssertCleanAction
    | GitCleanIgnoredAction
    | GitDiffAction
    | GitCheckoutAction
    | GitCreateBranchAction
    | GitAddAction
    | GitCommitAction
    | GitNativeAction,
    Field(discriminator="operation"),
]


class GitResultContract(StrEnum):
    EXIT_CODE = "exit-code"
    CLEAN_WORKTREE = "clean-worktree"
