from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DurableSchemaDisposition(StrEnum):
    """Version relationship of one observed durable/config schema to this build."""

    UNVERSIONED = "UNVERSIONED"
    CURRENT = "CURRENT"
    STALE = "STALE"
    FUTURE = "FUTURE"


@dataclass(frozen=True, slots=True)
class DurableSchemaPolicy:
    """Current-version policy owner for durable/config schemas."""

    name: str
    current: int

    def __post_init__(self) -> None:
        if self.current < 1:
            raise ValueError("durable schema current version must be >= 1")

    def classify(self, found: int) -> DurableSchemaDisposition:
        if found == 0:
            return DurableSchemaDisposition.UNVERSIONED
        if found == self.current:
            return DurableSchemaDisposition.CURRENT
        if found > self.current:
            return DurableSchemaDisposition.FUTURE
        return DurableSchemaDisposition.STALE

    def version_status_text(self, found: int) -> str:
        disposition = self.classify(found)
        if disposition is DurableSchemaDisposition.CURRENT:
            return f"{self.name} schema {found} is current"
        if disposition is DurableSchemaDisposition.FUTURE:
            return (
                f"{self.name} schema {found} is newer than this build supports "
                f"(current {self.current})"
            )
        if disposition is DurableSchemaDisposition.UNVERSIONED:
            return f"{self.name} is unversioned; current schema is {self.current}"
        return f"{self.name} schema {found} is unsupported; current schema is {self.current}"
