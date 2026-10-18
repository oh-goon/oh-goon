from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Mapping

STEP_START_TIMEOUT_ENV = "OH_GOON_RUNTIME_ACCEPTANCE_STEP_START_TIMEOUT_SECONDS"
INTERRUPTIBLE_STEP_ENV = "OH_GOON_RUNTIME_ACCEPTANCE_INTERRUPTIBLE_STEP_SECONDS"
RESOLVED_TIMING_ENV = "OH_GOON_RUNTIME_ACCEPTANCE_RESOLVED_TIMING"
_SCHEMA = "oh-goon-runtime-acceptance-timing/v1"


@dataclass(frozen=True)
class RuntimeAcceptanceTiming:
    step_start_timeout_seconds: float = 180.0
    interruptible_step_seconds: float = 300.0

    @property
    def cancellation_window_seconds(self) -> float:
        return self.interruptible_step_seconds - self.step_start_timeout_seconds

    @classmethod
    def resolve(cls, environment: Mapping[str, str]) -> "RuntimeAcceptanceTiming":
        resolved = environment.get(RESOLVED_TIMING_ENV)
        if resolved is not None:
            return cls._from_resolved(resolved)
        timing = cls(
            step_start_timeout_seconds=_positive_seconds(
                environment.get(STEP_START_TIMEOUT_ENV), STEP_START_TIMEOUT_ENV, 180.0
            ),
            interruptible_step_seconds=_positive_seconds(
                environment.get(INTERRUPTIBLE_STEP_ENV), INTERRUPTIBLE_STEP_ENV, 300.0
            ),
        )
        timing.validate()
        return timing

    @classmethod
    def _from_resolved(cls, raw: str) -> "RuntimeAcceptanceTiming":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{RESOLVED_TIMING_ENV} is not valid JSON") from exc
        if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
            raise RuntimeError(f"{RESOLVED_TIMING_ENV} has an unsupported schema")
        expected = {
            "schema",
            "step_start_timeout_seconds",
            "interruptible_step_seconds",
            "authority_digest",
        }
        if set(value) != expected:
            raise RuntimeError(f"{RESOLVED_TIMING_ENV} contains unexpected or missing fields")
        supplied_digest = value["authority_digest"]
        if not isinstance(supplied_digest, str):
            raise RuntimeError(f"{RESOLVED_TIMING_ENV} authority digest must be a string")
        authority_payload = {
            "schema": value["schema"],
            "step_start_timeout_seconds": value["step_start_timeout_seconds"],
            "interruptible_step_seconds": value["interruptible_step_seconds"],
        }
        if supplied_digest != _authority_digest(authority_payload):
            raise RuntimeError(f"{RESOLVED_TIMING_ENV} authority digest does not match resolved timing")
        timing = cls(
            step_start_timeout_seconds=float(value["step_start_timeout_seconds"]),
            interruptible_step_seconds=float(value["interruptible_step_seconds"]),
        )
        timing.validate()
        return timing

    def validate(self) -> None:
        for name, value in (
            ("step_start_timeout_seconds", self.step_start_timeout_seconds),
            ("interruptible_step_seconds", self.interruptible_step_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise RuntimeError(f"runtime acceptance {name} must be finite and positive")
        if self.cancellation_window_seconds <= 0:
            raise RuntimeError(
                f"{INTERRUPTIBLE_STEP_ENV} must be greater than {STEP_START_TIMEOUT_ENV} "
                "so cancellation has a stable observation window"
            )

    def serialized(self) -> str:
        self.validate()
        payload = {"schema": _SCHEMA, **asdict(self)}
        return json.dumps(
            {**payload, "authority_digest": _authority_digest(payload)},
            sort_keys=True,
            separators=(",", ":"),
        )

    def summary(self) -> str:
        return (
            "runtime acceptance timing: "
            f"step_start={self.step_start_timeout_seconds:g}s, "
            f"cancellation_window={self.cancellation_window_seconds:g}s, "
            f"interruptible_fixture={self.interruptible_step_seconds:g}s"
        )


def _positive_seconds(raw: str | None, name: str, default: float) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive finite number of seconds, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive finite number of seconds, got {raw!r}")
    return value


def _authority_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
