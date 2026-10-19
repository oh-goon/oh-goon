from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from oh_goon.runtime.run_admission_lock import serialize_run_admission


def test_same_admission_identity_is_single_flight_across_threads(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    first_entered = Event()
    release_first = Event()
    order: list[str] = []
    order_lock = Lock()

    def first() -> None:
        with serialize_run_admission(database_path, "run:stable"):
            with order_lock:
                order.append("first-enter")
            first_entered.set()
            assert release_first.wait(timeout=2)
            with order_lock:
                order.append("first-exit")

    def second() -> None:
        assert first_entered.wait(timeout=2)
        with serialize_run_admission(database_path, "run:stable"):
            with order_lock:
                order.append("second-enter")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        assert first_entered.wait(timeout=2)
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert order == ["first-enter", "first-exit", "second-enter"]
    assert len(list((tmp_path / ".admission-locks").glob("admission-*.lock"))) == 1


def test_admission_lock_rehardens_existing_lock_directory_and_file_owner_only(
    tmp_path: Path,
) -> None:
    import os
    import stat

    if os.name == "nt":
        return
    lock_dir = tmp_path / ".admission-locks"
    lock_dir.mkdir(mode=0o755)
    lock_dir.chmod(0o755)

    with serialize_run_admission(tmp_path / "state.db", "run:stable"):
        lock_file = next(lock_dir.glob("admission-*.lock"))
        lock_file.chmod(0o644)

    # Reopening must repair both directory and the existing lock file.
    with serialize_run_admission(tmp_path / "state.db", "run:stable"):
        lock_file = next(lock_dir.glob("admission-*.lock"))
        assert stat.S_IMODE(lock_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600
