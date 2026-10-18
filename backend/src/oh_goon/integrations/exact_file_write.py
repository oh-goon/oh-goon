from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


from oh_goon.core.path_authority import ensure_private_directory

from oh_goon.core.file_digest import sha256_file

class ExactPublicationDurabilityError(OSError):
    """Publication became visible but its directory durability could not be proven."""


if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


@contextmanager
def exact_file_write_lock(target: Path) -> Iterator[None]:
    """Serialize cooperating writers for one exact local target path."""
    lock_root = _exact_file_lock_root()
    identity = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    with (lock_root / f"{identity}.lock").open("a+b") as stream:
        _acquire_lock(stream)
        try:
            yield
        finally:
            _release_lock(stream)



def _exact_file_lock_root() -> Path:
    """Return one private cooperating-lock directory for the current OS user."""

    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    return ensure_private_directory(
        Path(tempfile.gettempdir()) / f"oh-goon-exact-file-locks{suffix}"
    )

def exact_file_revision(target: Path) -> str:
    """Return the SHA-256 identity of the exact bytes currently committed at target."""
    return sha256_file(target)


def require_expected_revision(
    current_revision: str | None,
    expected_revision: str | None,
    *,
    changed_error: Callable[[str, str | None], Exception],
    removed_error: Callable[[], Exception],
) -> None:
    """Enforce one exact-revision compare-and-swap proof rule.

    Existing state requires an explicit matching revision. Missing state is valid only
    for an unfenced create. Domain callers supply their own typed stale errors.
    """
    if current_revision is None:
        if expected_revision is not None:
            raise removed_error()
        return
    if expected_revision is None or current_revision != expected_revision:
        raise changed_error(current_revision, expected_revision)


def require_expected_exact_file_revision(
    target: Path,
    expected_revision: str | None,
    *,
    invalid_error: Callable[[], Exception],
    changed_error: Callable[[str, str | None], Exception],
    removed_error: Callable[[], Exception],
) -> None:
    """Apply the canonical revision proof to one exact regular-file target."""
    if not target.exists():
        require_expected_revision(
            None,
            expected_revision,
            changed_error=changed_error,
            removed_error=removed_error,
        )
        return
    if target.is_symlink() or not target.is_file():
        raise invalid_error()
    require_expected_revision(
        exact_file_revision(target),
        expected_revision,
        changed_error=changed_error,
        removed_error=removed_error,
    )


def publish_text_file(target: Path, content: str, *, newline: str | None = None) -> None:
    """Atomically publish a complete text artifact; overwrite semantics remain caller-owned."""
    with exact_file_write_lock(target):
        with prepared_text_file(target, content, newline=newline) as temporary:
            replace_prepared_file(temporary, target)


def replace_prepared_file(temporary: Path, target: Path) -> None:
    """Commit prepared bytes and durably publish the directory entry where supported."""
    os.replace(temporary, target)
    _sync_published_parent(target)


def publish_prepared_directory(staging: Path, target: Path) -> None:
    """Publish one prepared directory without making immutable source modes construction authority.

    Moving a directory between parents requires owner write permission on the moved
    directory on Linux because its ``..`` entry changes.  A prepared content tree may
    intentionally have a read-only root mode (for example 0555).  Temporarily add only
    owner-write permission to the unpublished staging root, rename it, then restore the
    exact prepared root mode before the target becomes durable.  Child modes and bytes
    are never changed.
    """
    prepared_mode = stat.S_IMODE(staging.stat(follow_symlinks=False).st_mode)
    publish_mode = prepared_mode | stat.S_IWUSR
    if publish_mode != prepared_mode:
        staging.chmod(publish_mode)
    try:
        staging.rename(target)
    except BaseException:
        if staging.exists() and publish_mode != prepared_mode:
            staging.chmod(prepared_mode)
        raise
    try:
        target.chmod(prepared_mode)
    except OSError as exc:
        # The object must not remain published with construction permission if mode
        # restoration fails. Best-effort rollback keeps a retry from accepting a
        # writable root merely because its content hash still matches.
        try:
            target.rename(staging)
            staging.chmod(prepared_mode)
        except OSError as rollback_error:
            raise ExactPublicationDurabilityError(
                f"Published {target} but could not restore its prepared directory mode "
                f"or roll publication back: {rollback_error}"
            ) from exc
        raise
    try:
        _fsync_directory(target)
        _sync_published_parent(target)
    except OSError as exc:
        raise ExactPublicationDurabilityError(
            f"Published {target} but could not durably sync its restored directory mode: {exc}"
        ) from exc


def sync_prepared_path(path: Path) -> None:
    """Fsync one complete prepared file/tree before publishing it for cross-run reuse."""
    if os.name == "nt":
        return
    if path.is_file() and not path.is_symlink():
        _fsync_regular(path)
        return
    for current, _directories, files in os.walk(path, topdown=False, followlinks=False):
        root = Path(current)
        for name in files:
            entry = root / name
            if not entry.is_symlink():
                _fsync_regular(entry)
        _fsync_directory(root)


def _sync_published_parent(target: Path) -> None:
    try:
        _fsync_directory(target.parent)
    except OSError as exc:
        raise ExactPublicationDurabilityError(
            f"Published {target} but could not durably sync its parent directory: {exc}"
        ) from exc


def remove_file_durably(target: Path) -> bool:
    """Remove one exact file/symlink and durably publish its absence where supported."""
    observed = os.path.lexists(target)
    if not observed:
        return False
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    if os.path.lexists(target):
        raise OSError(errno.EIO, f"Exact file deletion did not remove {target}")
    _fsync_directory(target.parent)
    return True


def _fsync_regular(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
        _drop_clean_file_cache(descriptor)
    finally:
        os.close(descriptor)


def _drop_clean_file_cache(descriptor: int) -> None:
    """Best-effort release after durable publication; never part of correctness."""

    advise = getattr(os, "posix_fadvise", None)
    dontneed = getattr(os, "POSIX_FADV_DONTNEED", None)
    if advise is None or dontneed is None:
        return
    try:
        advise(descriptor, 0, 0, dontneed)
    except OSError:
        return


def _fsync_directory(directory: Path) -> None:
    if sys.platform == "win32":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.ENOTSUP}
            if hasattr(errno, "EOPNOTSUPP"):
                unsupported.add(errno.EOPNOTSUPP)
            if exc.errno not in unsupported:
                raise
    finally:
        os.close(descriptor)


@contextmanager
def prepared_text_file(target: Path, content: str, *, newline: str | None = None) -> Iterator[Path]:
    """Prepare and fsync replacement bytes beside target; caller decides whether to commit them."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        yield temporary
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_lock(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _release_lock(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
