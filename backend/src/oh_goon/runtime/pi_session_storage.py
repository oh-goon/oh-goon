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
from typing import BinaryIO, Literal

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    PiSessionCleanupUnverified,
    PiSessionHistoryUnqualified,
    PiSessionIdentityMismatch,
    PiSessionUnavailable,
)
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PiSessionDirectoryPin,
    PiSessionFileObservation,
    PiSessionFilePin,
    PiSessionRetirementDirectoryIdentity,
    PiSessionRetirementPin,
    PiSessionSaveObservation,
    encode_pi_session_entry,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import (
    pi_session_storage_id,
    repository_subject_instance_id_from_metadata,
)
from oh_goon.engine.command import MAX_COMMAND_INPUT_BYTES
from oh_goon.integrations.agent_native_execution import decode_native_agent_record

_MAX_PI_SESSION_BYTES = 16 * 1024 * 1024
_PI_STORAGE_ID = re.compile(r"pi-session-[0-9a-f]{64}")


def require_pi_session_repository(workspace: Path) -> None:
    """Refuse unsupported repository layouts before native preflight."""
    try:
        mode = (workspace / ".git").lstat().st_mode
    except OSError:
        mode = 0
    if not stat.S_ISDIR(mode):
        raise PiSessionUnavailable("New persistent Pi sessions require a direct Git repository")


def _require_pi_personal_storage_separation(storage: Path) -> None:
    try:
        personal = Path.home() / ".pi"
        personal_roots = (personal, personal.resolve(), (personal / "agent").resolve())
    except (OSError, RuntimeError) as exc:
        raise FleetPlanInvalid("Default Pi personal storage could not be established") from exc
    if any(storage.is_relative_to(root) or root.is_relative_to(storage) for root in personal_roots):
        raise FleetPlanInvalid("Private Pi session storage overlaps default Pi personal storage")


def _mount_id(fd: int) -> bytes:
    with Path(f"/proc/self/fdinfo/{fd}").open("rb") as stream:
        raw = stream.read(8193)
    mount_ids = [
        line.split(b":", 1)[1].strip() for line in raw.splitlines() if line.startswith(b"mnt_id:")
    ]
    if len(raw) > 8192 or len(mount_ids) != 1 or not mount_ids[0].isdigit():
        raise FleetPlanInvalid("Linux Pi session mount identity is unavailable")
    return mount_ids[0]


def _require_direct_filesystem(*mount_ids: bytes, workspace: Path | None = None) -> None:
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

    if workspace is not None:
        # mountinfo escapes the four characters that cannot appear literally
        # in a field. Bubblewrap recursively binds the workspace directory.
        prefix = os.fsencode(workspace)
        for character, escaped in (
            (b"\\", rb"\134"),
            (b" ", rb"\040"),
            (b"\t", rb"\011"),
            (b"\n", rb"\012"),
        ):
            prefix = prefix.replace(character, escaped)
        prefix = prefix.rstrip(b"/") + b"/"
        if any(row[4].startswith(prefix) for row in rows):
            raise FleetPlanInvalid(
                "Pi session workspace requires a direct filesystem without nested mounts"
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
    ):
        raise FleetPlanInvalid(
            "Pi session storage differs from the admitted direct Git workspace and Step"
        )
    if (storage_id is None) != (prior_file is None) or (
        storage_id is not None and not _PI_STORAGE_ID.fullmatch(storage_id)
    ):
        raise FleetPlanInvalid("Pi session reopening requires the exact retained storage receipt")
    if prior_file is not None and prior_file.sha256 is None:
        raise PiSessionHistoryUnqualified("Retained Pi session has no qualified byte receipt")
    continuing = prior_file is not None
    leaf = storage_id or pi_session_storage_id(
        fleet_run_id, repository.repository_id, step_id, attempt_number
    )
    _require_pi_personal_storage_separation(layout.pi_sessions)
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
                _require_pi_personal_storage_separation(layout.pi_sessions)
                check_state()
                check_workspace()
                _require_direct_filesystem(*(state_mounts | workspace_mounts), workspace=workspace)
                observed_git = os.open(".git", _directory_flags(), dir_fd=workspace_fd)
                try:
                    if _directory_identity(observed_git) != expected_git:
                        raise FleetPlanInvalid("Pi session Git root changed before use")
                finally:
                    os.close(observed_git)
                try:
                    os.stat("commondir", dir_fd=git_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FleetPlanInvalid("Pi session requires repository-local Git metadata")
                if (
                    repository_subject_instance_id_from_metadata(
                        workspace, os.fstat(workspace_fd), os.fstat(git_fd)
                    )
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
                try:
                    _observed, metadata = _observe_pinned_session_file(
                        directory_fd,
                        name,
                        fd,
                        require_original_storage_current,
                        expected_file=prior_file,
                    )
                except FleetPlanInvalid as exc:
                    raise PiSessionHistoryUnqualified(
                        "Retained Pi session bytes could not be verified"
                    ) from exc

                def require_before_launch() -> None:
                    try:
                        current = _require_current_file(directory_fd, name, fd)
                    except OSError as exc:
                        raise PiSessionUnavailable(
                            "Retained Pi session is unavailable before launch"
                        ) from exc
                    except FleetPlanInvalid as exc:
                        raise PiSessionIdentityMismatch(
                            "Retained Pi session file identity changed before launch"
                        ) from exc
                    if _file_state(current) != _file_state(metadata):
                        raise PiSessionHistoryUnqualified(
                            "Retained Pi session changed before launch"
                        )

                file_pin = PiSessionFilePin(fd, require_before_launch)
            else:
                with os.scandir(directory_fd) as entries:
                    if next(entries, None) is not None:
                        raise FleetPlanInvalid(
                            "New Pi session requires an empty original directory"
                        )
        except (OSError, ValueError) as exc:
            if continuing:
                raise PiSessionUnavailable("Retained Pi session storage is unavailable") from exc
            raise FleetPlanInvalid("Private Pi session storage is unavailable") from exc
        yield PiSessionDirectoryPin(
            path=layout.pi_sessions / leaf,
            directory_fd=directory_fd,
            workspace=workspace,
            workspace_fd=workspace_fd,
            git_fd=git_fd,
            require_current=require_original_storage_current,
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


def _session_file_present(directory_fd: int, name: str) -> bool:
    """The owned slot is either empty or contains its single selected native file."""
    with os.scandir(directory_fd) as entries:
        entry = next(entries, None)
        if entry is None:
            return False
        if entry.name != name or next(entries, None) is not None:
            raise FleetPlanInvalid("Private Pi session directory contains unrelated entries")
    return True


def _require_current_file(directory_fd: int, name: str, fd: int) -> os.stat_result:
    if not _session_file_present(directory_fd, name):
        raise FileNotFoundError("Original Pi session file is absent")
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


def _read_save_record(stream: BinaryIO, remaining: int) -> tuple[bytes, dict[str, object]]:
    limit = min(remaining, MAX_COMMAND_INPUT_BYTES)
    record = stream.readline(limit + 1)
    if len(record) > limit or not record.endswith(b"\n"):
        raise FleetPlanInvalid("Pi save contains an incomplete or oversized JSONL record")
    try:
        return record, decode_native_agent_record(record.decode("utf-8"))
    except (AgentProviderError, UnicodeError) as exc:
        raise FleetPlanInvalid("Pi save contains an invalid JSONL record") from exc


def _verify_and_sync_save(
    stream: BinaryIO,
    directory_fd: int,
    before: os.stat_result,
    save: PiSessionSaveObservation,
    prior_file: PiSessionFileObservation | None,
) -> str:
    """Bind Pi's final append evidence to the same file and directory we synchronize."""
    digest = hashlib.sha256()
    remaining = before.st_size
    if prior_file is None:
        header, metadata = _read_save_record(stream, remaining)
        if metadata["type"] != "session" or metadata.get("id") != save.session_id:
            raise FleetPlanInvalid("Pi save header differs from the original native session")
        digest.update(header)
        remaining -= len(header)
    else:
        if (
            prior_file.save_verification != "pi_rpc_synced"
            or prior_file.sha256 is None
            or not 1 <= prior_file.size < before.st_size
            or (prior_file.device, prior_file.inode) != (before.st_dev, before.st_ino)
        ):
            raise FleetPlanInvalid("Pi save has no exact qualified predecessor extent")
        remaining_prefix = prior_file.size
        while remaining_prefix:
            chunk = stream.read(min(1024 * 1024, remaining_prefix))
            if not chunk:
                raise FleetPlanInvalid("Pi save is missing its retained predecessor bytes")
            digest.update(chunk)
            remaining_prefix -= len(chunk)
        if digest.hexdigest() != prior_file.sha256:
            raise FleetPlanInvalid("Pi save changed its retained predecessor bytes")
        remaining -= prior_file.size

    entries_digest = hashlib.sha256()
    for _ in range(save.entry_count):
        record, entry = _read_save_record(stream, remaining)
        try:
            entries_digest.update(encode_pi_session_entry(entry))
        except (TypeError, ValueError, RecursionError) as exc:
            raise FleetPlanInvalid("Pi save contains an invalid JSONL record") from exc
        digest.update(record)
        remaining -= len(record)
    if remaining or stream.read(1) or entries_digest.hexdigest() != save.entries_sha256:
        raise FleetPlanInvalid("Pi save bytes differ from the observed native append")

    os.fsync(stream.fileno())
    os.fsync(directory_fd)
    return digest.hexdigest()


def _observe_pinned_session_file(
    directory_fd: int,
    name: str,
    fd: int,
    require_current: Callable[[], None],
    *,
    expected_file: PiSessionFileObservation | None = None,
    save: PiSessionSaveObservation | None = None,
    prior_file: PiSessionFileObservation | None = None,
) -> tuple[PiSessionFileObservation, os.stat_result]:
    require_current()
    before = _require_current_file(directory_fd, name, fd)
    if expected_file is not None and (before.st_dev, before.st_ino) != (
        expected_file.device,
        expected_file.inode,
    ):
        raise PiSessionIdentityMismatch(
            "Retained Pi session file identity differs from its admitted receipt"
        )
    if expected_file is not None and before.st_size != expected_file.size:
        raise PiSessionHistoryUnqualified("Retained Pi session size differs from its receipt")
    if not 1 <= before.st_size <= _MAX_PI_SESSION_BYTES:
        raise FleetPlanInvalid("Pi session exceeds its bounded persistence observation scope")
    save_verification = expected_file.save_verification if expected_file is not None else None
    byte_count = before.st_size
    # The owned O_PATH handle was verified regular before opening a readable
    # descriptor. Never reopen a caller-controlled pathname to read bytes.
    with Path(f"/proc/self/fd/{fd}").open("rb") as stream:
        if _file_state(os.fstat(stream.fileno())) != _file_state(before):
            raise FleetPlanInvalid("Original Pi session file changed before hashing")
        if save is not None:
            sha256 = _verify_and_sync_save(stream, directory_fd, before, save, prior_file)
            save_verification = "pi_rpc_synced"
        else:
            digest = hashlib.sha256()
            byte_count = 0
            while chunk := stream.read(min(1024 * 1024, before.st_size + 1 - byte_count)):
                byte_count += len(chunk)
                digest.update(chunk)
            sha256 = digest.hexdigest()
    require_current()
    after = _require_current_file(directory_fd, name, fd)
    if byte_count != before.st_size or _file_state(after) != _file_state(before):
        raise FleetPlanInvalid("Original Pi session file changed while hashing")
    observed = PiSessionFileObservation(
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        sha256=sha256,
        save_verification=save_verification,
    )
    if expected_file is not None and observed != expected_file:
        raise PiSessionHistoryUnqualified("Retained Pi session differs from its admitted receipt")
    return observed, after


def observe_pi_session_file(
    pin: PiSessionDirectoryPin,
    name: str,
    save: PiSessionSaveObservation | None = None,
    *,
    prior_file: PiSessionFileObservation | None = None,
) -> PiSessionFileObservation:
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
    if save is not None and (
        name != PI_SESSION_NEW_FILE.name or (pin.existing_file is None) != (prior_file is None)
    ):
        raise FleetPlanInvalid("Pi save differs from the original native session and predecessor")
    try:
        with ExitStack() as owned:
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
                pin.directory_fd,
                name,
                fd,
                pin.require_current,
                save=save,
                prior_file=prior_file,
            )
            return observation
    except OSError as exc:
        raise FleetPlanInvalid("Original Pi session file is unavailable") from exc


def inspect_recovered_pi_session_storage(
    *, layout: ApplicationStatePathLayout, storage_id: str
) -> Literal["orphaned", "unavailable", "unverified"]:
    """Observe occupancy only in the slot selected by an already reconciled writer."""
    if (
        sys.platform != "linux"
        or not hasattr(os, "O_PATH")
        or not _PI_STORAGE_ID.fullmatch(storage_id)
    ):
        return "unverified"

    with ExitStack() as descriptors:
        try:
            _require_pi_personal_storage_separation(layout.pi_sessions)
            directory_fd, check_state, state_mounts = _pin_path(layout.root, descriptors)
            state_info = os.fstat(directory_fd)
            if state_info.st_uid != os.geteuid() or stat.S_IMODE(state_info.st_mode) & 0o022:
                return "unverified"
            checks = [check_state]

            def require_current() -> None:
                _require_pi_personal_storage_separation(layout.pi_sessions)
                for check in checks:
                    check()
                _require_direct_filesystem(*state_mounts)

            for component in (layout.pi_sessions.name, storage_id):
                try:
                    directory_fd, check_child = _open_private_child(
                        descriptors, directory_fd, component, create=False
                    )
                except FileNotFoundError:
                    require_current()
                    try:
                        os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        return "unavailable"
                    return "unverified"
                checks.append(check_child)

            require_current()
            name = PI_SESSION_NEW_FILE.name
            if not _session_file_present(directory_fd, name):
                require_current()
                return "unverified" if _session_file_present(directory_fd, name) else "unavailable"
            fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
            descriptors.callback(os.close, fd)
            before = _require_current_file(directory_fd, name, fd)
            require_current()
            after = _require_current_file(directory_fd, name, fd)
            return "orphaned" if _file_state(before) == _file_state(after) else "unverified"
        except (OSError, ValueError, FleetPlanInvalid):
            return "unverified"


@contextmanager
def pin_pi_session_retirement(
    *,
    layout: ApplicationStatePathLayout,
    storage_id: str,
    expected_file: PiSessionFileObservation,
    expected_directory: PiSessionRetirementDirectoryIdentity | None = None,
) -> Generator[PiSessionRetirementPin, None, None]:
    """Pin the exact settled file for an explicitly requested Journal retirement.

    An unrecorded request requires present, proven bytes. A recorded request
    may verify absence on the same retained directory after an interrupted
    unlink. The caller must invoke removal inside its current Journal cut.
    """
    if sys.platform != "linux" or not hasattr(os, "O_PATH"):
        raise PiSessionUnavailable("Pi session retirement requires Linux directory custody")
    if not _PI_STORAGE_ID.fullmatch(storage_id):
        raise PiSessionIdentityMismatch("Pi retirement requires its original private storage")
    if expected_file.sha256 is None or not 1 <= expected_file.size <= _MAX_PI_SESSION_BYTES:
        raise PiSessionHistoryUnqualified("Pi retirement requires an exact retained byte receipt")

    with ExitStack() as descriptors:
        try:
            _require_pi_personal_storage_separation(layout.pi_sessions)
            state_fd, check_state, state_mounts = _pin_path(layout.root, descriptors)
            state_info = os.fstat(state_fd)
            if state_info.st_uid != os.geteuid() or stat.S_IMODE(state_info.st_mode) & 0o022:
                raise PiSessionIdentityMismatch("Pi retirement requires owned application state")
            root_fd, check_root = _open_private_child(
                descriptors, state_fd, layout.pi_sessions.name, create=False
            )
            directory_fd, check_directory = _open_private_child(
                descriptors, root_fd, storage_id, create=False
            )

            def require_current() -> None:
                _require_pi_personal_storage_separation(layout.pi_sessions)
                check_state()
                check_root()
                check_directory()
                _require_direct_filesystem(*state_mounts)

            require_current()
            directory_info = os.fstat(directory_fd)
            directory = PiSessionRetirementDirectoryIdentity(
                device=directory_info.st_dev, inode=directory_info.st_ino
            )
            if expected_directory is not None and directory != expected_directory:
                raise PiSessionIdentityMismatch("Original Pi retirement directory changed")
            name = PI_SESSION_NEW_FILE.name
            retained: tuple[int, os.stat_result] | None = None
            if _session_file_present(directory_fd, name):
                fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
                descriptors.callback(os.close, fd)
                _observed, metadata = _observe_pinned_session_file(
                    directory_fd, name, fd, require_current, expected_file=expected_file
                )
                retained = (fd, metadata)
            elif expected_directory is None:
                raise PiSessionUnavailable("Original Pi session file is absent before retirement")
        except FleetPlanInvalid as exc:
            raise PiSessionIdentityMismatch(
                "Original Pi retirement storage could not be pinned"
            ) from exc
        except OSError as exc:
            raise PiSessionUnavailable("Original Pi retirement storage is unavailable") from exc

        def remove() -> None:
            try:
                require_current()
                if _session_file_present(directory_fd, name):
                    if retained is None:
                        raise PiSessionIdentityMismatch(
                            "A file appeared in the Pi retirement directory"
                        )
                    file_fd, original = retained
                    current = _require_current_file(directory_fd, name, file_fd)
                    if _file_state(current) != _file_state(original):
                        raise PiSessionIdentityMismatch(
                            "Original Pi retirement file changed before removal"
                        )
                    os.unlink(name, dir_fd=directory_fd)
                if retained is not None and os.fstat(retained[0]).st_nlink != 0:
                    raise PiSessionIdentityMismatch("Original Pi retirement file remains linked")
                # Retain the private directory as the binding for retry after a
                # successful unlink with no accepted Journal observation yet.
                os.fsync(directory_fd)
                require_current()
                if _session_file_present(directory_fd, name):
                    raise PiSessionIdentityMismatch(
                        "Original Pi retirement file absence is unproven"
                    )
            except FleetPlanInvalid as exc:
                raise PiSessionIdentityMismatch("Original Pi retirement storage changed") from exc
            except OSError as exc:
                raise PiSessionCleanupUnverified(
                    "Original Pi retirement cleanup is unverified"
                ) from exc

        yield PiSessionRetirementPin(directory=directory, remove=remove)
