"""DB6 pre-work rejection tests: hosted, no mount privileges or live ENOSPC."""
from __future__ import annotations

from pathlib import Path
import pytest

from tests.native.test_db6_kernel_enospc_witness import _mountinfo_fstype, _require_disposable_mount


def test_db6_mountinfo_rejects_shared_mount_and_wrong_filesystem(tmp_path: Path) -> None:
    assert _mountinfo_fstype(Path('/db6'), '36 25 0:42 / /db6 rw - tmpfs tmpfs rw') == 'tmpfs'
    with pytest.raises(RuntimeError, match='dedicated'):
        _mountinfo_fstype(Path('/db6/subdir'), '36 25 0:42 / /db6 rw - tmpfs tmpfs rw')
    with pytest.raises(RuntimeError, match='dedicated'):
        _mountinfo_fstype(Path('/db6'), '36 25 0:42 / /db6 rw - overlay overlay rw')
    with pytest.raises(RuntimeError, match='dedicated'):
        _mountinfo_fstype(Path('/db6'), '36 25 0:42 / /db6 rw - tmpfs tmpfs rw\n37 25 0:43 / /db6 rw - tmpfs tmpfs rw')


def test_db6_fail_before_work_on_non_mount_and_symlink(tmp_path: Path) -> None:
    original = tmp_path / 'not-a-mount'
    original.mkdir(mode=0o700)
    bad_link = tmp_path / 'alias'
    bad_link.symlink_to(original, target_is_directory=True)
    with pytest.raises(RuntimeError, match='nonsymlink'):
        _require_disposable_mount(bad_link, '')
    with pytest.raises(RuntimeError, match='dedicated'):
        _require_disposable_mount(original, '')
    assert list(original.iterdir()) == []


def test_db6_rejects_nonempty_mount_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mount = tmp_path / 'apparently-owned'
    mount.mkdir(mode=0o700)
    (mount / 'another-user-file').write_bytes(b'unchanged')
    monkeypatch.setattr('tests.native.test_db6_kernel_enospc_witness._mountinfo_fstype', lambda *_: 'tmpfs')
    with pytest.raises(RuntimeError, match='empty'):
        _require_disposable_mount(mount, '')
    assert (mount / 'another-user-file').read_bytes() == b'unchanged'
