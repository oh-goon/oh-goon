"""Explicit native Git source coordinates, distinct from execution authority."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel


class GoonGitSource(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    git: str = Field(min_length=1, max_length=2000)
    ref: str = Field(min_length=1, max_length=240)
    path: str = Field(min_length=1, max_length=500)

    @field_validator("git", "ref")
    @classmethod
    def printable_argument(cls, value: str) -> str:
        if value.startswith("-") or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError("Git source and ref must be printable non-option arguments")
        return value

    @field_validator("git")
    @classmethod
    def explicit_origin(cls, value: str) -> str:
        if Path(value).is_absolute():
            return value
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"https", "http", "ssh", "git", "file"}:
                raise ValueError("Unsupported Git source protocol")
            if parsed.password is not None or (
                parsed.username is not None and parsed.scheme in {"http", "https"}
            ):
                raise ValueError(
                    "Git credentials belong in native configuration, not source coordinates"
                )
            if parsed.query or parsed.fragment:
                raise ValueError("Git source coordinates cannot contain query or fragment values")
            if parsed.scheme != "file" and not parsed.hostname:
                raise ValueError("Git source URL requires an explicit host")
            if parsed.scheme == "file" and (
                parsed.netloc not in {"", "localhost"} or not Path(parsed.path).is_absolute()
            ):
                raise ValueError("Git file source must name an absolute local repository")
            return value
        if "@" in value and ":" in value and not value.startswith("ext:"):
            username, _, destination = value.rpartition("@")
            host, separator, path = destination.partition(":")
            if not username or ":" in username or not host or not separator or not path:
                raise ValueError(
                    "Git credentials belong in native configuration, not source coordinates"
                )
            return value
        raise ValueError("Git source must be an explicit URL or absolute repository path")

    @field_validator("ref")
    @classmethod
    def one_ref(cls, value: str) -> str:
        if ":" in value or value.startswith("^") or "*" in value:
            raise ValueError("Git ref must select one branch, tag or commit")
        return value

    @field_validator("path")
    @classmethod
    def contained_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("Git source path must be contained in the selected repository")
        return path.as_posix()


class LockedGoonSource(GoonGitSource):
    kind: Literal["goon", "plan"] = "goon"
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    plan: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def selected_kind(self) -> LockedGoonSource:
        if self.kind == "goon":
            if self.plan is not None:
                raise ValueError("A locked Goon source cannot select a Plan")
        elif self.plan is None or not self.plan or PurePosixPath(self.plan).name == "goon.yml":
            raise ValueError("A locked Plan source requires an exact Plan path")
        else:
            normalized = self.contained_path(self.plan)
            if normalized != self.plan or normalized == ".":
                raise ValueError("A locked Plan source requires a canonical contained Plan path")
        return self
