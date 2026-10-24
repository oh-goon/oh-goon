"""Linux immutable-directory entry custody for the original Agent staging owner.

An immutable directory inode denies changes to entries even by its ordinary owner;
this does not immunize against privileged CAP_LINUX_IMMUTABLE actors, parent
renames, or prove Agent process confinement. Opt-in ONLY, with no fallback.
"""

from __future__ import annotations

import fcntl
import os
import stat
import struct
import sys

from oh_goon.core.errors import FleetPlanInvalid

# Linux UAPI <linux/fs.h>: _IOR('f', 1, long), _IOW('f', 2, long).
# Both ioctl number AND userspace buffer size use native sizeof(long),
# 4 bytes on a 32-bit Linux process and 8 bytes on a 64-bit one.
_LONG_BYTES = struct.calcsize("@l")
_FS_IOC_GETFLAGS = 0x80006601 | (_LONG_BYTES << 16)
_FS_IOC_SETFLAGS = 0x40006602 | (_LONG_BYTES << 16)
_FS_IMMUTABLE_FL = 0x00000010


def _directory_flags(fd: int) -> int:
    if sys.platform != "linux" or not stat.S_ISDIR(os.fstat(fd).st_mode):
        raise FleetPlanInvalid("Linux immutable-directory custody requires a directory")
    buf = bytearray(struct.pack("@l", 0))
    try:
        fcntl.ioctl(fd, _FS_IOC_GETFLAGS, buf, True)
    except OSError as exc:
        raise FleetPlanInvalid("Linux directory immutable flags unavailable") from exc
    return int(struct.unpack("@l", buf)[0])


def require_original_agent_directory_immutable(fd: int) -> None:
    """Read actual kernel directory flags from the already-owned opened inode."""
    if not _directory_flags(fd) & _FS_IMMUTABLE_FL:
        raise FleetPlanInvalid("Agent staged directory lacks kernel immutable custody")


def enable_original_agent_directory_immutable(fd: int) -> None:
    """Set immutable on a sealed stage directory; never downgrade on failure.

    CAP_LINUX_IMMUTABLE and filesystem support are required by Linux. Only the
    original staged-source owner calls this after writing and fsyncing entries.
    """
    flags = _directory_flags(fd)
    if not flags & _FS_IMMUTABLE_FL:
        try:
            fcntl.ioctl(fd, _FS_IOC_SETFLAGS, struct.pack("@l", flags | _FS_IMMUTABLE_FL))
        except OSError as exc:
            raise FleetPlanInvalid("Linux directory immutable custody unavailable") from exc
    require_original_agent_directory_immutable(fd)


def disable_original_agent_directory_immutable(fd: int) -> None:
    """Drop only FS_IMMUTABLE_FL on an already-open original staged directory.

    This is an inode operation, never a pathname deletion, a grant, or proof of
    process quiescence. Only the original prework stager's *failed, unpublished*
    construction path may invoke it; post-admission LACE teardown is separate.
    """
    flags = _directory_flags(fd)
    if flags & _FS_IMMUTABLE_FL:
        try:
            fcntl.ioctl(fd, _FS_IOC_SETFLAGS, struct.pack("@l", flags & ~_FS_IMMUTABLE_FL))
        except OSError as exc:
            raise FleetPlanInvalid("Linux Agent immutable directory rollback unavailable") from exc
    if _directory_flags(fd) & _FS_IMMUTABLE_FL:
        raise FleetPlanInvalid("Linux Agent immutable directory rollback not confirmed")


def rollback_unpublished_agent_immutable_stage(stage_fd: int) -> None:
    """Unseal only validated original staging directories, without deleting files.

    Called by the original Fleet stager only if constructing its *new* private
    stage fails before returning a receipt. First validate the *entire* bounded
    inode tree so an unexpected symlink, hardlink, device or changed owner
    cannot trigger partial unsealing of unrelated objects. Keep all FDs open
    until rollback ends. A failed unseal leaves the non-reusable stage for
    privileged operator recovery, never a successful prework receipt.
    """
    if sys.platform != "linux":
        raise FleetPlanInvalid("Linux Agent directory rollback requires Linux")
    open_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    owned: list[int] = []
    entries = 0
    root = os.fstat(stage_fd)
    if (
        not stat.S_ISDIR(root.st_mode)
        or root.st_uid != os.geteuid()
        or stat.S_IMODE(root.st_mode) not in (0o700, 0o500)
    ):
        raise FleetPlanInvalid("Original Agent staging root not privately owned for rollback")

    def walk(fd: int) -> None:
        nonlocal entries
        st = os.fstat(fd)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_dev != root.st_dev
            or st.st_uid != root.st_uid
            or stat.S_IMODE(st.st_mode) not in (0o700, 0o500)
        ):
            raise FleetPlanInvalid("Unexpected Agent staged directory during rollback")
        owned.append(os.dup(fd))
        for name in sorted(os.listdir(fd)):
            entries += 1
            if entries > 16384 or not name or name in (".", "..") or "/" in name:
                raise FleetPlanInvalid("Agent rollback directory entries exceed bound")
            child_st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if child_st.st_dev != root.st_dev or child_st.st_uid != root.st_uid:
                raise FleetPlanInvalid("Agent rollback source ownership changed")
            if stat.S_ISDIR(child_st.st_mode):
                child_fd = os.open(name, open_flags, dir_fd=fd)
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (child_st.st_dev, child_st.st_ino):
                        raise FleetPlanInvalid("Agent rollback directory was substituted")
                    walk(child_fd)
                finally:
                    os.close(child_fd)
            elif (
                not stat.S_ISREG(child_st.st_mode)
                or child_st.st_nlink != 1
                or stat.S_IMODE(child_st.st_mode) not in (0o600, 0o400)
            ):
                raise FleetPlanInvalid("Unsafe Agent staged file during rollback")

    try:
        walk(stage_fd)
        for fd in reversed(owned):
            disable_original_agent_directory_immutable(fd)
    except OSError as exc:
        raise FleetPlanInvalid("Original Agent staged directory rollback failed closed") from exc
    finally:
        for fd in owned:
            os.close(fd)
