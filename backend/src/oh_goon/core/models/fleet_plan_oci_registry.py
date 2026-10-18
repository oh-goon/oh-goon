from __future__ import annotations

import re

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class AuthoringOciRegistryCredentials(ImmutableModel):
    """Names of declared host-backed values used only for one private registry authfile."""

    username_env: str = Field(pattern=_ENV_NAME.pattern)
    password_env: str = Field(pattern=_ENV_NAME.pattern)

    @model_validator(mode="after")
    def validate_distinct_names(self) -> AuthoringOciRegistryCredentials:
        if self.username_env == self.password_env:
            raise ValueError("Fleet OCI registry username/password env names must be distinct")
        return self


class OciRegistryCredentials(ImmutableModel):
    """Resolved credential provenance contains names only; secret bytes never enter the plan."""

    username_host_env: str = Field(pattern=_ENV_NAME.pattern)
    password_host_env: str = Field(pattern=_ENV_NAME.pattern)

    @model_validator(mode="after")
    def validate_distinct_names(self) -> OciRegistryCredentials:
        if self.username_host_env == self.password_host_env:
            raise ValueError("Fleet OCI registry username/password host env names must be distinct")
        return self

    @property
    def host_environment_names(self) -> tuple[str, str]:
        return (self.username_host_env, self.password_host_env)
