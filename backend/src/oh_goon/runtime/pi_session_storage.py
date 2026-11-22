"""Private native Pi files scoped to one original Run and Step attempt."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import PiSessionUnavailable
from oh_goon.core.models.pi_session_storage import PiSessionDirectoryPin, PiSessionFileObservation
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import pi_session_storage_id, repository_subject_instance_id


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
    owned: ExitStack, parent: int, name: str, *, exclusive: bool
) -> tuple[int, Callable[[], None]]:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
        created = True
    except FileExistsError:
        if exclusive:
            raise FleetPlanInvalid("Original Pi session attempt directory already exists") from None
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
def create_pi_session_directory(
    *,
    layout: ApplicationStatePathLayout,
    fleet_run_id: str,
    repository: RepositoryIdentity,
    repository_admission: PlanRepositoryAdmissionEvidence,
    step_id: str,
    attempt_number: int,
    workspace: Path,
    require_current: Callable[[], None],
) -> Generator[PiSessionDirectoryPin, None, None]:
    """Allocate a fresh private directory under already admitted Run/Step authority.

    The caller holds original process custody through the pin's scope. Once
    created, the leaf is retained even on failure. Reuse requires a separately
    qualified admission and cannot be inferred from filesystem contents.
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
    leaf = pi_session_storage_id(fleet_run_id, repository.repository_id, step_id, attempt_number)
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
                owned, state_fd, layout.pi_sessions.name, exclusive=False
            )
            require_original_current()
            check_root()
            directory_fd, check_directory = _open_private_child(
                owned, root_fd, leaf, exclusive=True
            )

            def require_original_storage_current() -> None:
                try:
                    require_original_current()
                    check_root()
                    check_directory()
                except (OSError, ValueError) as exc:
                    raise FleetPlanInvalid("Original Pi session storage is unavailable") from exc

            require_original_storage_current()
            with os.scandir(directory_fd) as entries:
                if next(entries, None) is not None:
                    raise FleetPlanInvalid("New Pi session requires an empty original directory")
        except (OSError, ValueError) as exc:
            raise FleetPlanInvalid("Private Pi session storage is unavailable") from exc
        yield PiSessionDirectoryPin(
            layout.pi_sessions / leaf, directory_fd, workspace, require_original_storage_current
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
            fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=pin.directory_fd)
            owned.callback(os.close, fd)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or _mount_id(fd) != identity[-1]
            ):
                raise FleetPlanInvalid(
                    "Pi session file is not an original singly owned regular file"
                )
            pin.require_current()
            named = os.stat(name, dir_fd=pin.directory_fd, follow_symlinks=False)
            if named != info or named.st_ctime_ns != info.st_ctime_ns:
                raise FleetPlanInvalid("Original Pi session file changed before observation")
            return PiSessionFileObservation(
                device=info.st_dev, inode=info.st_ino, size=info.st_size
            )
        except OSError as exc:
            raise FleetPlanInvalid("Original Pi session file is unavailable") from exc
