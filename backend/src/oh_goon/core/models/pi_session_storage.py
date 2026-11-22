"""Process-local transport of one original Pi session directory handle."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

PI_SESSION_SANDBOX_DIRECTORY = PurePosixPath("/run/oh-goon-pi-session")


@dataclass(frozen=True, slots=True)
class PiSessionDirectoryPin:
    """A borrowed directory handle retained by the original storage scope."""

    path: Path
    directory_fd: int = field(repr=False)
    workspace: Path
    require_current: Callable[[], None] = field(repr=False)
