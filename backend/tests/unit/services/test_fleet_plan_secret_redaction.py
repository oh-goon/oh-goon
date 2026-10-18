from __future__ import annotations

from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart, ResolvedEnvironmentBinding
from oh_goon.integrations.fleet_plan_secret_redaction import (
    redact_host_secret_text,
    streaming_host_secret_redactor,
)


def test_transient_output_redacts_host_backed_step_values_before_projection() -> None:
    step = ResolvedCommandStep(
        step_id="publish",
        argv=("echo", "publish"),
        timeout_seconds=30,
        environment=(
            ResolvedEnvironmentBinding(
                name="TOKEN",
                parts=(HostEnvironmentPart(name="TOKEN"),),
            ),
        ),
    )

    redacted = redact_host_secret_text(
        step,
        "token=super-secret value",
        {"TOKEN": "super-secret"},
    )

    assert redacted == "token=*** value"


def test_streaming_redaction_masks_secret_split_across_transport_chunks() -> None:
    step = ResolvedCommandStep(
        step_id="publish",
        argv=("echo", "publish"),
        timeout_seconds=30,
        environment=(
            ResolvedEnvironmentBinding(
                name="TOKEN",
                parts=(HostEnvironmentPart(name="TOKEN"),),
            ),
        ),
    )
    redactor = streaming_host_secret_redactor(step, {"TOKEN": "super-secret"})

    parts = [
        redactor.feed("token=super-"),
        redactor.feed("secret value\n"),
        redactor.flush(),
    ]

    assert "".join(parts) == "token=*** value\n"
    assert "super-" not in "".join(parts)
