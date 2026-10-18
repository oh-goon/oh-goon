from __future__ import annotations

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel

GameTapeDurableEvidence = Literal[
    "run_lifecycle",
    "repository_lifecycle",
    "step_lifecycle",
    "timing",
    "command",
    "provenance",
    "output_fingerprint",
    "failure_signature",
    "runtime_authority",
    "termination_quiescence",
    "recovery_race",
    "output_acceptance",
    "side_effect_disposition",
]
GameTapeTransientEvidence = Literal["raw_stdout", "raw_stderr"]


class GameTapeObservabilityReceipt(ImmutableModel):
    """Explicit evidence lifecycle and authority guarantees for one Game Tape."""

    durable_evidence: tuple[GameTapeDurableEvidence, ...]
    transient_evidence: tuple[GameTapeTransientEvidence, ...]
    raw_output_max_chunks: int = Field(ge=1)
    raw_output_max_chunk_chars: int = Field(ge=1)
    terminal_handoff_seconds: int = Field(ge=1)
    raw_output_survives_process_restart: Literal[False] = False
    persistent_raw_output_store: Literal[False] = False
    persistent_search_index: Literal[False] = False
    ui_execution_authority: Literal[False] = False
    execution_order_authority: Literal["resolved_plan"] = "resolved_plan"
    failure_navigation_source: Literal["durable_failure_evidence"] = "durable_failure_evidence"
