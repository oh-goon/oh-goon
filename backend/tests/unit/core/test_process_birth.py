from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.process_birth import (
    ProcessBirthIdentity,
    observe_process_birth,
    observe_process_liveness,
    observe_process_state,
    process_birth_matches,
)


def _proc_tree(
    tmp_path: Path,
    *,
    pid: int,
    boot_id: str,
    start_ticks: int,
    comm: str = "python worker",
    state: str = "S",
) -> Path:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True, exist_ok=True)
    (proc / "sys/kernel/random/boot_id").write_text(boot_id + "\n", encoding="utf-8")
    (proc / str(pid)).mkdir(exist_ok=True)
    # tail begins at stat field 3. starttime is field 22 => tail index 19.
    tail = [state] + [str(index) for index in range(4, 22)] + [str(start_ticks), "0"]
    (proc / str(pid) / "stat").write_text(
        f"{pid} ({comm}) " + " ".join(tail) + "\n",
        encoding="utf-8",
    )
    return proc


def test_linux_proc_birth_identity_uses_boot_and_start_ticks_not_pid_alone(tmp_path: Path) -> None:
    proc = _proc_tree(
        tmp_path, pid=77, boot_id="boot-a", start_ticks=123456, comm="name ) with spaces"
    )
    identity = observe_process_birth(77, proc_root=proc)
    assert identity == ProcessBirthIdentity(pid=77, boot_id="boot-a", start_ticks=123456)
    assert process_birth_matches(identity, proc_root=proc)


def test_pid_reuse_is_rejected_when_start_ticks_change(tmp_path: Path) -> None:
    proc = _proc_tree(tmp_path, pid=77, boot_id="boot-a", start_ticks=10)
    expected = observe_process_birth(77, proc_root=proc)
    assert expected is not None
    _proc_tree(tmp_path, pid=77, boot_id="boot-a", start_ticks=20)
    assert not process_birth_matches(expected, proc_root=proc)


def test_missing_or_unsupported_procfs_returns_unknown_not_false_terminal(tmp_path: Path) -> None:
    assert observe_process_birth(77, proc_root=tmp_path / "missing-proc") is None


def test_process_liveness_does_not_treat_zombie_pid_as_live(tmp_path: Path) -> None:
    live = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1, state="S")
    assert observe_process_liveness(88, proc_root=live) is True
    _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1, state="Z")
    assert observe_process_liveness(88, proc_root=live) is False
    (live / "88" / "stat").unlink()
    assert observe_process_liveness(88, proc_root=live) is False
    assert observe_process_liveness(88, proc_root=tmp_path / "missing-proc") is None


def test_state_observation_keeps_missing_distinct_from_a_recorded_terminal_state(
    tmp_path: Path,
) -> None:
    proc = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1)
    assert observe_process_state(88, proc_root=proc) == "S"
    _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1, state="Z")
    assert observe_process_state(88, proc_root=proc) == "Z"
    (proc / "88/stat").unlink()
    assert observe_process_state(88, proc_root=proc) is None
    assert observe_process_state(88, proc_root=tmp_path / "unavailable") is None
    assert observe_process_liveness(88, proc_root=proc) is False


@pytest.mark.parametrize("bad_stat", ["88 (worker) Z", "wrong pid", "89 (worker) Z"])
def test_state_observation_refuses_unqualified_process_records(
    tmp_path: Path, bad_stat: str
) -> None:
    proc = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1)
    (proc / "88/stat").write_text(bad_stat)
    assert observe_process_state(88, proc_root=proc) is None


def test_state_observation_reads_the_native_record_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1)
    native_read = Path.read_text
    reads: list[Path] = []

    def observed(path: Path, **kwargs: str) -> str:
        reads.append(path)
        return native_read(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", observed)
    assert observe_process_state(88, proc_root=proc) == "S"
    assert reads == [proc / "88/stat"]
    reads.clear()
    assert observe_process_birth(88, proc_root=proc) == ProcessBirthIdentity(
        pid=88, boot_id="boot-a", start_ticks=1
    )
    assert reads == [proc / "sys/kernel/random/boot_id", proc / "88/stat"]


@pytest.mark.parametrize("state", list("RSDZTtWXxKPI"))
def test_native_state_codes_preserve_birth_and_executable_liveness(
    tmp_path: Path, state: str
) -> None:
    proc = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1, state=state)
    assert observe_process_birth(88, proc_root=proc) == ProcessBirthIdentity(
        pid=88, boot_id="boot-a", start_ticks=1
    )
    assert observe_process_state(88, proc_root=proc) == state
    assert observe_process_liveness(88, proc_root=proc) is (state not in {"Z", "X", "x"})


def test_process_observations_refuse_shifted_stat_fields(tmp_path: Path) -> None:
    proc = _proc_tree(tmp_path, pid=88, boot_id="boot-a", start_ticks=1)
    assert observe_process_birth(88, proc_root=proc) is not None
    assert observe_process_state(88, proc_root=proc) == "S"
    path = proc / "88/stat"
    header, fields = path.read_text().rsplit(")", 1)
    path.write_text(header + ") " + " ".join(fields.split()[1:]) + "\n")
    assert observe_process_birth(88, proc_root=proc) is None
    assert observe_process_state(88, proc_root=proc) is None
    assert observe_process_liveness(88, proc_root=proc) is None
