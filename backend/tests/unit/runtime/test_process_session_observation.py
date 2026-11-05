"""Native session observation distinguishes positive disappearance from unknown inventory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oh_goon.runtime import process_group
from oh_goon.runtime.process_group import process_session_exists

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="native Linux process-session inventory"
)


@pytest.mark.parametrize("kernel_observation", ("retired", "foreign", "member", "unavailable"))
def test_disappearing_proc_entry_requires_its_native_session_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kernel_observation: str
) -> None:
    # Declared /proc fixture, not physical custody qualification. One original
    # zombie and one entry lost between directory inventory and metadata read.
    original = tmp_path / "100"
    original.mkdir()
    (original / "stat").write_text("100 (original command) Z 1 100 100\n")
    (tmp_path / "200").mkdir()
    monkeypatch.setattr(process_group, "Path", lambda _path: tmp_path)

    def native_session(pid: int) -> int:
        assert pid == 200
        if kernel_observation == "retired":
            raise ProcessLookupError("ESRCH: native process no longer exists")
        if kernel_observation == "unavailable":
            raise PermissionError("native observation unavailable")
        return 100 if kernel_observation == "member" else 300

    monkeypatch.setattr(process_group.os, "getsid", native_session)
    assert process_session_exists(100) is (kernel_observation in {"member", "unavailable"})


def test_inaccessible_proc_metadata_remains_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "100"
    original.mkdir()
    (original / "stat").write_text("100 (original command) Z 1 100 100\n")
    (tmp_path / "200").mkdir()
    monkeypatch.setattr(process_group, "Path", lambda _path: tmp_path)
    native_read = Path.read_text

    def inaccessible(path: Path, *args: object, **kwargs: object) -> str:
        if path.parent.name == "200":
            raise PermissionError("process metadata unavailable")
        return native_read(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", inaccessible)
    monkeypatch.setattr(
        process_group.os,
        "getsid",
        lambda _pid: pytest.fail("Inaccessible metadata must remain unverified"),
    )
    assert process_session_exists(100) is True
