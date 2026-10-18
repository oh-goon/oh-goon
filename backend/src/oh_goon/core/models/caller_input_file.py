"""Immutable caller-file provenance transported with the existing Fleet Plan target.

No input values, file bytes or authorization claims are present in this model.
"""
from __future__ import annotations

import re
from typing import Literal
from pathlib import Path, PurePosixPath

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CallerInputFileIdentity(ImmutableModel):
    """Host-local source observation; freshness is re-proved by Fleet owners."""

    caller_root: Path
    relative_path: str = Field(min_length=1, max_length=4096)
    sha256: str
    source_device: int = Field(ge=0)
    source_inode: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_source(self) -> CallerInputFileIdentity:
        candidate = PurePosixPath(self.relative_path)
        if (not self.caller_root.is_absolute() or not candidate.parts
                or candidate.is_absolute() or "\\" in self.relative_path
                or any(part in {".", "..", ""} for part in self.relative_path.split("/"))
                or candidate.as_posix() != self.relative_path):
            raise ValueError("caller input file identity requires an absolute root and contained relative path")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("caller input file identity requires lowercase SHA-256")
        return self


class ScheduledCallerFileReceipt(ImmutableModel):
    """Read-only origin receipt for a once-bound scheduled target, never a file reader.

    Source bytes are observed at create/replace, and the canonical resolved
    FleetPlanTarget is durably carried to all future occurrences. No source
    filesystem location, inode or caller values are transported here.
    """

    schema_version: Literal["schedule-caller-input/v1"] = "schedule-caller-input/v1"
    relative_path: str = Field(min_length=1, max_length=4096)
    sha256: str
    execution_digest: str

    @model_validator(mode="after")
    def validate_receipt(self) -> ScheduledCallerFileReceipt:
        path = PurePosixPath(self.relative_path)
        if (path.is_absolute() or "\\" in self.relative_path
                or not path.parts or any(part in {"", ".", ".."} for part in self.relative_path.split("/"))
                or path.as_posix() != self.relative_path):
            raise ValueError("scheduled caller source must have a contained relative path")
        if _SHA256.fullmatch(self.sha256) is None or not self.execution_digest.startswith("sha256:") or _SHA256.fullmatch(self.execution_digest[7:]) is None:
            raise ValueError("scheduled caller source requires exact SHA-256 identities")
        return self
