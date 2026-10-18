from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel


class UnmanagedApplicationLaunchReceipt(ImmutableModel):
    """Explicit proof that this run was admitted outside a managed Launch Manifest."""

    kind: Literal["unmanaged"] = "unmanaged"


class ManagedApplicationLaunchReceipt(ImmutableModel):
    """Immutable Launch Manifest facts captured exactly when RUN_ADMITTED is created."""

    kind: Literal["managed"] = "managed"
    launch_id: str = Field(min_length=1)
    owner_generation: int = Field(ge=1)
    backend_generation: int = Field(ge=1)
    profile: str = Field(min_length=1)
    resolved_intent_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    backend_service_generation: int = Field(ge=1)
    backend_pid: int = Field(gt=0)
    backend_birth_provider: str = Field(min_length=1)
    backend_boot_id: str = Field(min_length=1)
    backend_start_ticks: int = Field(ge=0)
    backend_startup: Literal["satisfied"] = "satisfied"
    observed_host: str | None = Field(default=None, min_length=1)
    observed_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_endpoint_pair(self) -> ManagedApplicationLaunchReceipt:
        if (self.observed_host is None) != (self.observed_port is None):
            raise ValueError("managed application launch endpoint must be fully observed or absent")
        return self


ApplicationLaunchReceipt = Annotated[
    UnmanagedApplicationLaunchReceipt | ManagedApplicationLaunchReceipt,
    Field(discriminator="kind"),
]
