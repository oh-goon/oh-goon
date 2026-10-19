"""Linux fs-verity file-byte custody for the original Agent source stager.

This is *file* immutability, not directory-entry custody, process containment,
mount isolation, or permission to execute an Agent Step. The caller must opt in;
unsupported filesystems fail closed instead of silently degrading to chmod.
"""

from __future__ import annotations

import fcntl
import os
import stat
import struct
import sys

from oh_goon.core.errors import FleetPlanInvalid

# Linux UAPI: linux/fsverity.h and linux/fs.h. No wrapper process or signer.
# _IOW('f',133,struct fsverity_enable_arg [128 bytes])
_FS_IOC_ENABLE_VERITY = 0x40806685
# _IOWR('f',134,struct fsverity_digest [4 bytes])
_FS_IOC_MEASURE_VERITY = 0xC0046686
_SHA256 = 1


def measure_original_agent_file_verity(fd: int) -> bytes:
    """Measure SHA-256 fs-verity descriptor digest of one already-open file.

    A successful measurement is an actual kernel assertion that file contents
    are fs-verity protected, not merely a matching userspace SHA-256 checksum.
    """
    if sys.platform != "linux" or not stat.S_ISREG(os.fstat(fd).st_mode):
        raise FleetPlanInvalid("Kernel fs-verity requires a Linux regular file")
    digest = bytearray(struct.pack("=HH", _SHA256, 32) + bytes(32))
    try:
        fcntl.ioctl(fd, _FS_IOC_MEASURE_VERITY, digest, True)
    except OSError as exc:
        raise FleetPlanInvalid("Agent staged file lacks kernel fs-verity custody") from exc
    algorithm, length = struct.unpack_from("=HH", digest)
    if algorithm != _SHA256 or length != 32:
        raise FleetPlanInvalid("Agent staged file has unsupported fs-verity digest")
    return bytes(digest[4:36])


def enable_original_agent_file_verity(fd: int) -> bytes:
    """Freeze the verified file in place through Linux FS_IOC_ENABLE_VERITY.

    Must be called by the original staging owner after the last writer is
    closed and byte-exact readback completes; never by a Plan/Agent author.
    """
    if sys.platform != "linux" or not stat.S_ISREG(os.fstat(fd).st_mode):
        raise FleetPlanInvalid("Kernel fs-verity requires a Linux regular file")
    # version=1, hash_algorithm=SHA256, default block size, no salt/signature,
    # and every reserved field zero. Kernel validates this ABI structure.
    args = struct.pack("=IIIIQIIQ11Q", 1, _SHA256, 0, 0, 0, 0, 0, 0, *([0] * 11))
    if len(args) != 128:
        raise FleetPlanInvalid("Invalid Linux fs-verity enable ABI")
    try:
        fcntl.ioctl(fd, _FS_IOC_ENABLE_VERITY, args)
    except OSError as exc:
        raise FleetPlanInvalid("Kernel fs-verity unavailable for Agent staged file") from exc
    return measure_original_agent_file_verity(fd)
