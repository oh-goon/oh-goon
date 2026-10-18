from oh_goon.core.game_tape_policy import (
    GAME_TAPE_MAX_CHUNK_CHARS,
    GAME_TAPE_MAX_CHUNKS_PER_RUN,
    GAME_TAPE_TERMINAL_RETENTION_SECONDS,
)
from oh_goon.core.models.game_tape_observability import GameTapeObservabilityReceipt


def project_observability_receipt() -> GameTapeObservabilityReceipt:
    return GameTapeObservabilityReceipt(
        durable_evidence=(
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
        ),
        transient_evidence=("raw_stdout", "raw_stderr"),
        raw_output_max_chunks=GAME_TAPE_MAX_CHUNKS_PER_RUN,
        raw_output_max_chunk_chars=GAME_TAPE_MAX_CHUNK_CHARS,
        terminal_handoff_seconds=GAME_TAPE_TERMINAL_RETENTION_SECONDS,
    )
