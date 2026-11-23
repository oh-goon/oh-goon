"""Pin private native Pi files for the current original Run and Step attempt."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import PiSessionUnavailable
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PiSessionDirectoryPin,
    PiSessionFileObservation,
    PiSessionFilePin,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import pi_session_storage_id, repository_subject_instance_id

_MAX_PI_SESSION_BYTES = 16 * 1024 * 1024


def require_pi_session_repository(workspace: Path) -> None:
    """Refuse unsupported repository layouts before native preflight."""
    try:
        mode = (workspace / ".git").lstat().st_mode
    except OSError:
        mode = 0
    if not stat.S_ISDIR(mode):
        raise PiSessionUnavailable("New persistent Pi sessions require a direct Git repository")


def _mount_id(fd: int) -> bytes:
    with Path(f"/proc/self/fdinfo/{fd}").open("rb") as stream:
        raw = stream.read(8193)
    mount_ids = [
        line.split(b":", 1)[1].strip() for line in raw.splitlines() if line.startswith(b"mnt_id:")
    ]
    if len(raw) > 8192 or len(mount_ids) != 1 or not mount_ids[0].isdigit():
        raise FleetPlanInvalid("Linux Pi session mount identity is unavailable")
    return mount_ids[0]


def _require_direct_filesystem(*mount_ids: bytes) -> None:
    with Path("/proc/self/mountinfo").open("rb") as stream:
        raw = stream.read(1_048_577)
    rows = [line.partition(b" - ")[0].split() for line in raw.splitlines()]
    if len(raw) > 1_048_576 or any(len(row) < 6 for row in rows):
        raise FleetPlanInvalid("Linux Pi session mount topology is unavailable")
    for mount_id in set(mount_ids):
        selected = [row for row in rows if row[0] == mount_id]
        if (
            len(selected) != 1
            or selected[0][3] != b"/"
            or any(row[2] == selected[0][2] and row[0] != mount_id for row in rows)
        ):
            raise FleetPlanInvalid(
                "Pi session scope requires a direct filesystem without bind aliases"
            )


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _directory_identity(fd: int) -> tuple[int, int, int, int, bytes]:
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, _mount_id(fd))


def _pin_path(path: Path, owned: ExitStack) -> tuple[int, Callable[[], None], set[bytes]]:
    if path.anchor != "/" or len(path.parts) > 32 or ".." in path.parts:
        raise FleetPlanInvalid("Pi session paths require bounded absolute native scope")
    flags = _directory_flags()
    parent = os.open("/", flags)
    owned.callback(os.close, parent)
    lineage = []
    mounts = {_mount_id(parent)}
    for name in path.parts[1:]:
        child = os.open(name, flags, dir_fd=parent)
        owned.callback(os.close, child)
        identity = _directory_identity(child)
        lineage.append((parent, name, child, identity))
        mounts.add(identity[-1])
        parent = child

    def require_path_current() -> None:
        for parent_fd, name, child_fd, expected in lineage:
            observed = os.open(name, flags, dir_fd=parent_fd)
            try:
                if (
                    _directory_identity(observed) != expected
                    or _directory_identity(child_fd) != expected
                ):
                    raise FleetPlanInvalid("Pi session directory ancestor changed before use")
            finally:
                os.close(observed)

    return parent, require_path_current, mounts


def _open_private_child(
    owned: ExitStack, parent: int, name: str, *, create: bool, exclusive: bool = False
) -> tuple[int, Callable[[], None]]:
    created = False
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            if exclusive:
                raise FleetPlanInvalid(
                    "Original Pi session attempt directory already exists"
                ) from None
    directory = os.open(name, _directory_flags(), dir_fd=parent)
    owned.callback(os.close, directory)
    expected = _directory_identity(directory)
    if expected[2:] != (stat.S_IFDIR | 0o700, os.geteuid(), _mount_id(parent)):
        raise FleetPlanInvalid(
            "Pi session directory requires private ownership and its original mount"
        )
    if created:
        os.fsync(directory)
        os.fsync(parent)

    def require_child_current() -> None:
        observed = os.open(name, _directory_flags(), dir_fd=parent)
        try:
            if (
                _directory_identity(observed) != expected
                or _directory_identity(directory) != expected
            ):
                raise FleetPlanInvalid("Original Pi session directory changed before use")
        finally:
            os.close(observed)

    return directory, require_child_current


@contextmanager
def pin_pi_session_directory(
    *,
    layout: ApplicationStatePathLayout,
    fleet_run_id: str,
    repository: RepositoryIdentity,
    repository_admission: PlanRepositoryAdmissionEvidence,
    step_id: str,
    attempt_number: int,
    workspace: Path,
    require_current: Callable[[], None],
    storage_id: str | None = None,
    prior_file: PiSessionFileObservation | None = None,
) -> Generator[PiSessionDirectoryPin, None, None]:
    """Pin new or exact retained storage under the current admitted writer.

    The caller holds original process custody through the pin's scope. A prior
    receipt comes from the Journal of the exact selected continuation lineage.
    """
    if sys.platform != "linux" or not hasattr(os, "O_PATH"):
        raise FleetPlanInvalid(
            "Private Pi session storage requires Linux directory and mount custody"
        )
    if (
        not 1 <= len(fleet_run_id) <= 160
        or not 1 <= len(step_id) <= 80
        or type(attempt_number) is not int
        or attempt_number < 1
        or repository_admission.repository_id != repository.repository_id
        or step_id not in repository_admission.active_step_ids
        or workspace != repository.path
        or workspace.is_relative_to(layout.root)
        or layout.root.is_relative_to(workspace)
        or layout.pi_sessions.is_relative_to(Path.home() / ".pi")
    ):
        raise FleetPlanInvalid(
            "Pi session storage differs from the admitted direct Git workspace and Step"
        )
    if (
        (storage_id is None) != (prior_file is None)
        or (storage_id is not None and not re.fullmatch(r"pi-session-[0-9a-f]{64}", storage_id))
        or (prior_file is not None and prior_file.sha256 is None)
    ):
        raise FleetPlanInvalid("Pi session reopening requires the exact retained storage receipt")
    continuing = prior_file is not None
    leaf = storage_id or pi_session_storage_id(
        fleet_run_id, repository.repository_id, step_id, attempt_number
    )
    with ExitStack() as owned:
        try:
            state_fd, check_state, state_mounts = _pin_path(layout.root, owned)
            workspace_fd, check_workspace, workspace_mounts = _pin_path(workspace, owned)
            git_fd = os.open(".git", _directory_flags(), dir_fd=workspace_fd)
            owned.callback(os.close, git_fd)
            expected_git = _directory_identity(git_fd)
            state_info = os.fstat(state_fd)
            if (
                state_info.st_uid != os.geteuid()
                or stat.S_IMODE(state_info.st_mode) & 0o022
                or expected_git[-1] != _mount_id(workspace_fd)
            ):
                raise FleetPlanInvalid("Pi storage requires owned state and a direct Git root")

            def require_original_current() -> None:
                require_current()
                check_state()
                check_workspace()
                _require_direct_filesystem(*(state_mounts | workspace_mounts))
                observed_git = os.open(".git", _directory_flags(), dir_fd=workspace_fd)
                try:
                    if _directory_identity(observed_git) != expected_git:
                        raise FleetPlanInvalid("Pi session Git root changed before use")
                finally:
                    os.close(observed_git)
                if (
                    repository_subject_instance_id(workspace)
                    != repository_admission.source_authority.subject_instance_id
                ):
                    raise FleetPlanInvalid("Pi session repository subject changed before use")

            require_original_current()
            root_fd, check_root = _open_private_child(
                owned, state_fd, layout.pi_sessions.name, create=not continuing
            )
            require_original_current()
            check_root()
            directory_fd, check_directory = _open_private_child(
                owned, root_fd, leaf, create=not continuing, exclusive=not continuing
            )

            def require_original_storage_current() -> None:
                try:
                    require_original_current()
                    check_root()
                    check_directory()
                except (OSError, ValueError) as exc:
                    raise FleetPlanInvalid("Original Pi session storage is unavailable") from exc

            require_original_storage_current()
            file_pin = None
            if prior_file is not None:
                name = PI_SESSION_NEW_FILE.name
                fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
                owned.callback(os.close, fd)
                observed, metadata = _observe_pinned_session_file(
                    directory_fd, name, fd, require_original_storage_current
                )
                if observed != prior_file:
                    raise FleetPlanInvalid("Retained Pi session differs from its admitted receipt")

                def require_before_launch() -> None:
                    try:
                        current = _require_current_file(directory_fd, name, fd)
                    except OSError as exc:
                        raise FleetPlanInvalid(
                            "Retained Pi session is unavailable before launch"
                        ) from exc
                    if _file_state(current) != _file_state(metadata):
                        raise FleetPlanInvalid("Retained Pi session changed before launch")

                file_pin = PiSessionFilePin(fd, require_before_launch)
            else:
                with os.scandir(directory_fd) as entries:
                    if next(entries, None) is not None:
                        raise FleetPlanInvalid(
                            "New Pi session requires an empty original directory"
                        )
        except (OSError, ValueError) as exc:
            raise FleetPlanInvalid("Private Pi session storage is unavailable") from exc
        yield PiSessionDirectoryPin(
            layout.pi_sessions / leaf,
            directory_fd,
            workspace,
            require_original_storage_current,
            existing_file=file_pin,
        )


def _file_state(info: os.stat_result) -> tuple[int, ...]:
    # Reading the retained bytes can update access time. It cannot update any
    # identity or change metadata used to fence the subsequent launch.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_current_file(directory_fd: int, name: str, fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or _mount_id(fd) != _mount_id(directory_fd)
    ):
        raise FleetPlanInvalid("Pi session file is not an original singly owned regular file")
    named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if _file_state(named) != _file_state(info):
        raise FleetPlanInvalid("Original Pi session file changed before observation")
    return info


def _observe_pinned_session_file(
    directory_fd: int, name: str, fd: int, require_current: Callable[[], None]
) -> tuple[PiSessionFileObservation, os.stat_result]:
    require_current()
    before = _require_current_file(directory_fd, name, fd)
    if not 1 <= before.st_size <= _MAX_PI_SESSION_BYTES:
        raise FleetPlanInvalid("Pi session exceeds its bounded persistence observation scope")
    digest = hashlib.sha256()
    byte_count = 0
    # The owned O_PATH handle was verified regular before opening a readable
    # descriptor. Never reopen a caller-controlled pathname to read bytes.
    with Path(f"/proc/self/fd/{fd}").open("rb") as stream:
        if _file_state(os.fstat(stream.fileno())) != _file_state(before):
            raise FleetPlanInvalid("Original Pi session file changed before hashing")
        while chunk := stream.read(min(1024 * 1024, before.st_size + 1 - byte_count)):
            byte_count += len(chunk)
            digest.update(chunk)
    require_current()
    after = _require_current_file(directory_fd, name, fd)
    if byte_count != before.st_size or _file_state(after) != _file_state(before):
        raise FleetPlanInvalid("Original Pi session file changed while hashing")
    return (
        PiSessionFileObservation(
            device=after.st_dev, inode=after.st_ino, size=after.st_size, sha256=digest.hexdigest()
        ),
        after,
    )


def observe_pi_session_file(pin: PiSessionDirectoryPin, name: str) -> PiSessionFileObservation:
    """Observe an exact Pi-supplied basename after the original process settles."""
    if (
        not name.endswith(".jsonl")
        or not 7 <= len(name) <= 255
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or name.startswith(".")
        or (pin.existing_file is not None and name != PI_SESSION_NEW_FILE.name)
    ):
        raise FleetPlanInvalid("Pi session file requires an exact native basename")
    with ExitStack() as owned:
        try:
            pin.require_current()
            identity = _directory_identity(pin.directory_fd)
            if identity[2:4] != (
                stat.S_IFDIR | 0o700,
                os.geteuid(),
            ):
                raise FleetPlanInvalid("Original Pi session directory changed before observation")
            if pin.existing_file is None:
                fd = os.open(
                    name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.directory_fd
                )
                owned.callback(os.close, fd)
            else:
                fd = pin.existing_file.fd
            observation, _metadata = _observe_pinned_session_file(
                pin.directory_fd, name, fd, pin.require_current
            )
            return observation
        except OSError as exc:
            raise FleetPlanInvalid("Original Pi session file is unavailable") from exc
