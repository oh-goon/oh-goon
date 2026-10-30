from pathlib import Path

import pytest

from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.engine.errors import OperationCancelledError


def test_streaming_digest_cancellation_stops_before_next_read_and_closes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.core.file_digest as module

    path = tmp_path / "payload"
    path.write_bytes(b"abcdef")
    monkeypatch.setattr(module, "_STREAM_CHUNK_BYTES", 2)
    stream = path.open("rb")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: stream)
    checks = 0

    def check_cancel() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            assert stream.tell() == 2
            raise OperationCancelledError("cancelled after the first chunk")

    with pytest.raises(OperationCancelledError):
        sha256_file_with_size(path, check_cancel=check_cancel)
    assert checks == 2
    assert stream.closed
