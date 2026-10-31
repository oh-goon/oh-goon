from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from oh_goon.core.file_digest import sha256_file
from oh_goon.core.path_authority import ensure_private_directory


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


def publish_prepared_file(temporary: Path, target: Path) -> None:
    """Exclusively publish a staged file through pinned, no-follow native directories.

    The caller owns a private staging directory immediately beneath the target's
    parent. Existing targets, including symlinks, are never replaced.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("Safe file export requires native directory-relative file operations")
    if not target.is_absolute() or ".." in target.parts or temporary.parent.parent != target.parent:
        raise ValueError("Prepared file publication requires contained sibling staging")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as owned:
        parent = os.open(target.anchor, flags)
        owned.callback(os.close, parent)
        for component in target.parent.parts[1:]:
            parent = os.open(component, flags, dir_fd=parent)
            owned.callback(os.close, parent)
        staging = os.open(temporary.parent.name, flags, dir_fd=parent)
        owned.callback(os.close, staging)
        source = os.open(temporary.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=staging)
        owned.callback(os.close, source)
        if not stat.S_ISREG(os.fstat(source).st_mode):
            raise OSError("Prepared export is not a regular file")
        os.fsync(source)
        os.link(
            temporary.name,
            target.name,
            src_dir_fd=staging,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
        try:
            _fsync_directory_descriptor(parent)
        except OSError as exc:
            raise ExactPublicationDurabilityError(
                f"Published {target} but could not durably sync its parent directory: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class PreparedDirectory:
    """One private staging resource held by its creating native descriptors."""

    path: Path
    descriptor: int
    require_current: Callable[[], None]

    def write_text(self, relative: str, content: str) -> None:
        from pathlib import PurePosixPath

        parts = PurePosixPath(relative).parts
        if (
            not parts
            or relative != PurePosixPath(relative).as_posix()
            or any(part in {".", "..", "/"} for part in parts)
        ):
            raise ValueError("Staged authoring files require canonical relative paths")
        self.require_current()
        with ExitStack() as owned:
            directory = self.descriptor
            for part in parts[:-1]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                directory = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                owned.callback(os.close, directory)
            descriptor = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        self.require_current()


@contextmanager
def prepared_sibling_directory(target: Path) -> Iterator[PreparedDirectory]:
    """Create and clean only an exact private sibling through pinned ancestors."""
    import secrets
    import shutil

    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("Safe authoring staging requires native directory-relative operations")
    if not target.is_absolute() or ".." in target.parts:
        raise ValueError("Authoring output requires an absolute canonical path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as owned:
        parent = os.open(target.anchor, flags)
        owned.callback(os.close, parent)
        lineage: list[tuple[int, str, int]] = []
        for component in target.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=parent)
            owned.callback(os.close, child)
            lineage.append((parent, component, child))
            parent = child
        name = ".oh-goon-init-" + secrets.token_hex(16)
        os.mkdir(name, mode=0o700, dir_fd=parent)
        source = os.open(name, flags, dir_fd=parent)
        owned.callback(os.close, source)
        lineage.append((parent, name, source))

        def require_current() -> None:
            for ancestor, component, descriptor in lineage:
                actual = os.stat(component, dir_fd=ancestor, follow_symlinks=False)
                expected = os.fstat(descriptor)
                if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                    raise OSError("Authoring staging directory changed before publication")

        try:
            require_current()
            yield PreparedDirectory(target.parent / name, source, require_current)
        finally:
            try:
                actual = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                expected = os.fstat(source)
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    shutil.rmtree(name, dir_fd=parent)


def publish_prepared_directory(
    staging: Path,
    target: Path,
    *,
    exclusive: bool = False,
    expected_staging_identity: tuple[int, int] | None = None,
) -> None:
    """Publish one prepared directory without making immutable source modes construction authority.

    Moving a directory between parents requires owner write permission on the moved
    directory on Linux because its ``..`` entry changes.  A prepared content tree may
    intentionally have a read-only root mode (for example 0555).  Temporarily add only
    owner-write permission to the unpublished staging root, rename it, then restore the
    exact prepared root mode before the target becomes durable.  Child modes and bytes
    are never changed.
    """
    if exclusive:
        _publish_prepared_directory_exclusively(
            staging, target, expected_staging_identity=expected_staging_identity
        )
        return
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


def _publish_prepared_directory_exclusively(
    staging: Path, target: Path, *, expected_staging_identity: tuple[int, int] | None = None
) -> None:
    """Commit one complete sibling tree using native atomic no-replace rename."""
    if sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("Atomic exclusive directory publication requires native renameat2")
    if (
        not target.is_absolute()
        or ".." in target.parts
        or staging.parent != target.parent
        or staging.name == target.name
    ):
        raise ValueError("Exclusive directory publication requires contained sibling staging")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as owned:
        parent = os.open(target.anchor, flags)
        owned.callback(os.close, parent)
        lineage: list[tuple[int, str, int]] = []
        for component in target.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=parent)
            owned.callback(os.close, child)
            lineage.append((parent, component, child))
            parent = child
        source = os.open(staging.name, flags, dir_fd=parent)
        owned.callback(os.close, source)
        actual_source = os.fstat(source)
        if (
            expected_staging_identity is not None
            and (actual_source.st_dev, actual_source.st_ino) != expected_staging_identity
        ):
            raise OSError("Owned authoring staging directory changed before publication")
        lineage.append((parent, staging.name, source))

        def require_current(*, published: bool = False) -> None:
            for ancestor, name, child in lineage[:-1] if published else lineage:
                actual = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                expected = os.fstat(child)
                if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                    raise OSError("Authoring output directory changed before publication")

        _sync_prepared_directory_descriptor(source)
        require_current()
        _rename_directory_no_replace(parent, staging.name, target.name)
        try:
            actual = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            expected = os.fstat(source)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise OSError("Published directory changed after its atomic commit")
            _fsync_directory_descriptor(source)
            _fsync_directory_descriptor(parent)
            require_current(published=True)
        except OSError as exc:
            raise ExactPublicationDurabilityError(
                f"Published {target} but could not confirm its durable directory authority: {exc}"
            ) from exc


def _rename_directory_no_replace(parent: int, source: str, target: str) -> None:
    """Invoke the kernel's RENAME_NOREPLACE authority, without a racy fallback."""
    import ctypes

    native = ctypes.CDLL(None, use_errno=True)
    rename = getattr(native, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "Native renameat2 is unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(target), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target)


def _sync_prepared_directory_descriptor(descriptor: int) -> None:
    """Sync an authored tree without following mutable path aliases."""
    with os.scandir(descriptor) as entries:
        for entry in entries:
            status = entry.stat(follow_symlinks=False)
            if not stat.S_ISDIR(status.st_mode) and not stat.S_ISREG(status.st_mode):
                raise OSError(
                    "Authored directory publication requires regular files and directories"
                )
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if stat.S_ISDIR(status.st_mode):
                flags |= os.O_DIRECTORY
            child = os.open(entry.name, flags, dir_fd=descriptor)
            try:
                if stat.S_ISDIR(status.st_mode):
                    _sync_prepared_directory_descriptor(child)
                else:
                    os.fsync(child)
            finally:
                os.close(child)
    _fsync_directory_descriptor(descriptor)


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
        _fsync_directory_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory_descriptor(descriptor: int) -> None:
    """Use native directory durability for a caller-owned pinned descriptor."""
    try:
        os.fsync(descriptor)
    except OSError as exc:
        unsupported = {errno.EINVAL, errno.ENOTSUP}
        if hasattr(errno, "EOPNOTSUPP"):
            unsupported.add(errno.EOPNOTSUPP)
        if exc.errno not in unsupported:
            raise


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
