from datetime import datetime, timedelta, timezone

from oh_goon.core.models.game_tape import GameTapeReceipt


def test_duration_is_derived_from_lifecycle_timestamps() -> None:
    started = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    receipt = GameTapeReceipt(
        accepted_at=started - timedelta(seconds=1),
        started_at=started,
        finished_at=started + timedelta(milliseconds=1250),
    )
    assert receipt.duration_ms == 1250
    assert receipt.model_dump(mode="json")["duration_ms"] == 1250
    source = receipt.model_dump(mode="json", exclude_computed_fields=True)
    assert "duration_ms" not in source
    assert "requirements_planned" not in source
