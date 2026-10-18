from __future__ import annotations

import errno

from oh_goon.integrations import _copy_transport as copy_transport


def test_drop_clean_file_cache_is_best_effort(monkeypatch) -> None:
    observed: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr(copy_transport.os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(
        copy_transport.os,
        "posix_fadvise",
        lambda fd, offset, length, advice: observed.append((fd, offset, length, advice)),
        raising=False,
    )

    copy_transport._drop_clean_file_cache(17)

    assert observed == [(17, 0, 0, 4)]


def test_drop_clean_file_cache_failure_never_changes_snapshot_correctness(monkeypatch) -> None:
    monkeypatch.setattr(copy_transport.os, "POSIX_FADV_DONTNEED", 4, raising=False)

    def fail(*_args) -> None:
        raise OSError(errno.EINVAL, "unsupported")

    monkeypatch.setattr(copy_transport.os, "posix_fadvise", fail, raising=False)
    copy_transport._drop_clean_file_cache(17)
