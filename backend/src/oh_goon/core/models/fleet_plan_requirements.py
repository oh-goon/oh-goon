from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.source_tree_policy import is_transient_source_path


class RepositoryValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


def _reject_transient_requirement_path(path: str) -> str:
    try:
        transient = is_transient_source_path(path)
    except ValueError:
        # RepositoryPathSpace remains the owner of invalid path topology diagnostics.
        return path
    if transient:
        raise ValueError(
            "repository requirements cannot target generated or operational source-excluded paths: "
            f"{path}"
        )
    return path


class RepositoryFileExistsRequirement(ImmutableModel):
    """Require one regular repository-relative file before RUN_ADMITTED."""

    kind: Literal["file_exists"] = "file_exists"
    path: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_source_path(self) -> RepositoryFileExistsRequirement:
        _reject_transient_requirement_path(self.path)
        return self


class RepositoryFolderExistsRequirement(ImmutableModel):
    """Require one repository-relative directory before RUN_ADMITTED."""

    kind: Literal["folder_exists"] = "folder_exists"
    path: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_source_path(self) -> RepositoryFolderExistsRequirement:
        _reject_transient_requirement_path(self.path)
        return self


class RepositoryPathExistsRequirement(ImmutableModel):
    """Require one repository-relative path to exist before a CI/CD run is admitted."""

    kind: Literal["path_exists"] = "path_exists"
    path: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_source_path(self) -> RepositoryPathExistsRequirement:
        _reject_transient_requirement_path(self.path)
        return self


class PyprojectValueRequirement(ImmutableModel):
    """Require one typed, non-ambient value in pyproject.toml before admission."""

    kind: Literal["pyproject_value"] = "pyproject_value"
    field: str = Field(
        pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$",
        min_length=1,
        max_length=240,
    )
    value_type: RepositoryValueType
    non_empty: bool = False
    pattern: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_non_empty(self) -> PyprojectValueRequirement:
        if self.non_empty and self.value_type is not RepositoryValueType.STRING:
            raise ValueError(
                "Fleet repository requirement non_empty is valid only for string values"
            )
        if self.pattern is not None:
            if self.value_type is not RepositoryValueType.STRING:
                raise ValueError(
                    "Fleet repository requirement pattern is valid only for string values"
                )
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"Fleet repository requirement pattern is invalid: {exc}") from exc
        return self


class UvProjectRequirement(ImmutableModel):
    """Derived requirement for one backend-owned locked UV project environment.

    The Goon author selects a repository-relative project and dependency group with ``uv:``. Oh Goon derives this
    requirement so YAML never mirrors package membership from pyproject.toml/uv.lock.
    """

    kind: Literal["uv_project"] = "uv_project"
    project: str = Field(default=".", min_length=1, max_length=240)
    group: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("UV project requirement path must be repository-relative")
        return path.as_posix()


RequirementCount = Annotated[int, Field(ge=0)]


RepositoryRequirement = Annotated[
    RepositoryFileExistsRequirement
    | RepositoryFolderExistsRequirement
    | RepositoryPathExistsRequirement
    | PyprojectValueRequirement
    | UvProjectRequirement,
    Field(discriminator="kind"),
]
