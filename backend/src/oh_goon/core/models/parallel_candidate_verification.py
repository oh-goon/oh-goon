"""Frozen acceptance inputs and settled evidence for one parallel candidate."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id

VERIFIER_STEP_ID = "verify-1"
VERIFIER_INPUT_ROOT = PurePosixPath("/oh-goon-verifier/input")
VERIFIER_SOURCE_ROOT = PurePosixPath("/oh-goon-verifier/source")


def is_parallel_verifier_step_id(step_id: str) -> bool:
    """Recognize the authored verifier leaf in its resolved Goon occurrence."""
    return step_id.rsplit(".", 1)[-1] == VERIFIER_STEP_ID


def _bounded_text(value: str, *, maximum: int) -> str:
    if not value or "\x00" in value or len(value.encode("utf-8")) > maximum:
        raise ValueError("Verification text is empty, unencodable or over its byte limit")
    return value


class FrozenVerifierInput(ImmutableModel):
    """Operator-provided UTF-8 input retained outside the writable candidate."""

    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
    content: str = Field(repr=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content(self) -> FrozenVerifierInput:
        content = self.content.encode("utf-8")
        if len(content) > 64 * 1024 or hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("Verifier input bytes do not match their bounded frozen identity")
        return self


class ParallelCandidateVerifier(ImmutableModel):
    """One explicit direct native command and its operator-owned adjudicating inputs."""

    acceptance: str
    executable: str
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arguments: tuple[str, ...] = Field(min_length=1, max_length=32)
    inputs: tuple[FrozenVerifierInput, ...] = Field(min_length=1, max_length=4, repr=False)

    @field_validator("acceptance")
    @classmethod
    def validate_acceptance(cls, value: str) -> str:
        return _bounded_text(value, maximum=4096)

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        path = PurePosixPath(_bounded_text(value, maximum=4096))
        if (
            not path.is_absolute()
            or path.as_posix() != value
            or ".." in path.parts
            or not path.is_relative_to("/usr")
        ):
            raise ValueError("Verifier executable must be an absolute native /usr path")
        return value

    @model_validator(mode="after")
    def validate_bindings(self) -> ParallelCandidateVerifier:
        names = {item.name for item in self.inputs}
        if len(names) != len(self.inputs):
            raise ValueError("Verifier input names must be unique")
        placeholders = {f"{{input:{name}}}" for name in names} | {"{candidate}"}
        if not placeholders.issubset(self.arguments):
            raise ValueError("Verifier argv must explicitly bind its candidate and every input")
        for value in self.arguments:
            _bounded_text(value, maximum=4096)
            if ("{" in value or "}" in value) and value not in placeholders:
                raise ValueError("Verifier arguments use an unknown placeholder")
        return self

    @property
    def argv(self) -> tuple[str, ...]:
        paths = {
            f"{{input:{item.name}}}": str(VERIFIER_INPUT_ROOT / item.name) for item in self.inputs
        }
        paths["{candidate}"] = str(VERIFIER_SOURCE_ROOT)
        return self.executable, *(paths.get(value, value) for value in self.arguments)

    @property
    def identity(self) -> str:
        return stable_id("parallel-candidate-verifier", self.model_dump(mode="json"))


class ParallelCandidateVerifierObservation(ImmutableModel):
    """The adjudicating Step's content binding, interpreted by its native settlement."""

    verifier_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)
    workspace_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    postcondition: Literal["UNCHANGED", "MISMATCH"]


class ParallelCandidateVerification(ImmutableModel):
    """Operator readback bound to the original verified Run and exact checkpoint."""

    fleet_run_id: str = Field(min_length=1)
    parallel_work_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    base_source_id: str = Field(min_length=1)
    verifier_id: str = Field(min_length=1)
    checkpoint_id: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_identity: str | None = None
    physical_execution_id: str | None = None
    native_outcome: Literal["PASS", "FAIL"] | None = None
    outcome: Literal["PASS", "FAIL", "INCOMPLETE"]
    reason: str = Field(min_length=1)


class ParallelCandidateExport(ImmutableModel):
    """Explicit delivery of the complete native patch for a verified candidate."""

    verification: ParallelCandidateVerification
    destination: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(gt=0)
    changed_paths: tuple[str, ...] = Field(min_length=1)
    cleanup: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"
