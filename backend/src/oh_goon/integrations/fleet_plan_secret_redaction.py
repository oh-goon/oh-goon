from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace

from oh_goon.core.models.fleet_plan import ResolvedPlanStep
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.fleet_plan_environment import step_host_environment_names


class StreamingSecretRedactor:
    """Redact exact protected values even when transport chunks split them."""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(sorted(secrets, key=len, reverse=True))
        self._buffer = ""
        self._tail_chars = max((len(value) for value in self._secrets), default=1) - 1

    def feed(self, text: str) -> str:
        if not self._secrets:
            return text
        self._buffer += text
        safe_chars = max(0, len(self._buffer) - self._tail_chars)
        return self._consume(safe_chars)

    def flush(self) -> str:
        if not self._secrets:
            return ""
        return self._consume(len(self._buffer))

    def _consume(self, limit: int) -> str:
        output: list[str] = []
        index = 0
        while index < limit:
            secret = next(
                (value for value in self._secrets if self._buffer.startswith(value, index)),
                None,
            )
            if secret is not None:
                output.append("***")
                index += len(secret)
            else:
                output.append(self._buffer[index])
                index += 1
        self._buffer = self._buffer[index:]
        return "".join(output)


def streaming_host_secret_redactor(
    step: ResolvedPlanStep,
    host_environment: Mapping[str, str] | None = None,
) -> StreamingSecretRedactor:
    return StreamingSecretRedactor(_host_secret_values(step, host_environment))


def redact_host_secrets(
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    host_environment: Mapping[str, str] | None = None,
) -> StepOutcome:
    host = os.environ if host_environment is None else host_environment
    return replace(
        outcome,
        stdout=redact_host_secret_text(step, outcome.stdout, host),
        stderr=redact_host_secret_text(step, outcome.stderr, host),
    )


def redact_host_secret_text(
    step: ResolvedPlanStep,
    text: str,
    host_environment: Mapping[str, str] | None = None,
) -> str:
    """Redact the exact host-backed values already protected in durable step evidence."""

    for value in _host_secret_values(step, host_environment):
        text = text.replace(value, "***")
    return text


def _host_secret_values(
    step: ResolvedPlanStep,
    host_environment: Mapping[str, str] | None,
) -> tuple[str, ...]:
    host = os.environ if host_environment is None else host_environment
    values = {host[name] for name in step_host_environment_names(step) if host.get(name)}
    return tuple(sorted(values, key=len, reverse=True))
