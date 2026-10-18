"""CO-R3U1: immutable requirements for ORIGINAL LACE native physical custody.

This is a demand/transport, deliberately NOT a permit or an issuer. A local
signature, stat sample, SQLite row or Python instance cannot prove exclusive
kernel mutation control. Only a later independently provisioned native owner
may turn a requirement into effect authority at the original effect boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OriginalNativeCustodyRequirement:
    run_id: str
    step_id: str
    control_epoch: int
    executor_generation: str | None
    attempt_identity: str
    parent_device: int | None
    parent_inode: int | None
    resource_device: int | None
    resource_inode: int | None

    def __post_init__(self) -> None:
        if not all(type(x) is str and bool(x) for x in (
            self.run_id, self.step_id, self.attempt_identity,
        )):
            raise ValueError("original native custody requires exact Run/Step/attempt binding")
        if type(self.control_epoch) is not int or self.control_epoch < 0:
            raise ValueError("original native custody requires a nonnegative trust epoch")
        if self.executor_generation is not None and (
            type(self.executor_generation) is not str or not self.executor_generation
        ):
            raise ValueError("original native custody has invalid executor generation")
        for value in (self.parent_device, self.parent_inode, self.resource_device, self.resource_inode):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("original native custody has invalid kernel object identity")

    @property
    def complete_scope(self) -> bool:
        """Scope completeness, never proof that the scoped authority exists."""
        return (self.executor_generation is not None and all(x is not None for x in (
            self.parent_device, self.parent_inode, self.resource_device, self.resource_inode,
        )))
