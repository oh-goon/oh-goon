"""SKILL-1/2: one project-local skill selector with pinned offline Gitlink proof.

The registry never acquires source bytes or authorizes execution. A Gitlink pin
is checked against Git object identities *for the bytes read by AG-01*; no
network operation or independent skill copier exists here.
"""

from __future__ import annotations

import configparser
import hashlib
import os
import re
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.git import GitService

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenSourceTree

_MAX_BYTES = 65536
_MAX_SKILLS = 128
_NAME = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*")
_SECTION = re.compile(r'skill "([^"]+)"')
_REVISION = re.compile(r"[0-9a-f]{40}")


def _valid_path(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith("/")
        and "\\" not in value
        and all(33 <= ord(c) <= 126 for c in value)
        and all(
            part and part not in {".", ".."} and not part.startswith(".")
            for part in value.split("/")
        )
        and str(PurePosixPath(value)) == value
        and ":" not in value
        and all(c.isalnum() or c in "-_./" for c in value)
    )


def _git(cwd: Path, *args: str, missing_ok: bool = False) -> bytes:
    """Git metadata through the original GitService/command-supervisor owner.

    No independent subprocess launcher, fetch, clone, hook or source-byte read.
    """
    try:
        result = GitService().run(
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "submodule.recurse=false",
            *args,
            cwd=cwd,
            timeout=10,
            env={
                **os.environ,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
        )
    except (OSError, RuntimeError) as exc:
        raise FleetPlanInvalid("SKILL-2 Git metadata is not locally accessible") from exc
    if result.returncode:
        if missing_ok:
            return b""
        raise FleetPlanInvalid("SKILL-2 Git metadata is missing or invalid")
    stdout = result.stdout.encode("utf-8")
    if len(stdout) > _MAX_BYTES * 16:
        raise FleetPlanInvalid("SKILL-2 Git metadata exceeds bound")
    return stdout


@dataclass(frozen=True, slots=True)
class GitlinkSkillPin:
    path: str
    gitlink: str
    revision: str

    @property
    def reference(self) -> str:
        return f"gitlink:{self.revision}:{self.path}"

    def _context(self, root: Path) -> tuple[Path, str]:
        if root.is_symlink() or not root.is_dir():
            raise FleetPlanInvalid("SKILL-2 authorized skill root is not a regular directory")
        root = root.resolve(strict=True)
        actual = _git(root, "rev-parse", "--show-toplevel").decode().strip()
        if Path(actual) != root:
            raise FleetPlanInvalid("SKILL-2 skill root must be the Git superproject root")
        if _git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
            raise FleetPlanInvalid("SKILL-2 requires a SHA-1 Gitlink repository")
        entry = _git(root, "ls-tree", "-z", "HEAD", "--", self.gitlink)
        match = re.fullmatch(
            rb"160000 commit ([0-9a-f]{40})\t" + re.escape(self.gitlink.encode()) + rb"\x00",
            entry,
        )
        if match is None or match.group(1).decode() != self.revision:
            raise FleetPlanInvalid("SKILL-2 declared revision does not match committed Gitlink")
        modules_bytes = _git(root, "show", "HEAD:.gitmodules")
        if len(modules_bytes) > _MAX_BYTES:
            raise FleetPlanInvalid("SKILL-2 committed .gitmodules exceeds bound")
        try:
            modules = configparser.ConfigParser(interpolation=None, strict=True)
            modules.read_string(modules_bytes.decode("utf-8"))
        except (UnicodeError, configparser.Error) as exc:
            raise FleetPlanInvalid("SKILL-2 invalid committed .gitmodules") from exc
        if sum(modules[s].get("path") == self.gitlink for s in modules.sections()) != 1:
            raise FleetPlanInvalid("SKILL-2 Gitlink lacks unique committed .gitmodules mapping")
        checkout = root / self.gitlink
        if checkout.is_symlink() or not checkout.is_dir():
            raise FleetPlanInvalid("SKILL-2 Gitlink checkout missing or symlinked")
        if Path(_git(checkout, "rev-parse", "--show-toplevel").decode().strip()) != checkout:
            raise FleetPlanInvalid("SKILL-2 Gitlink checkout is not an independent repository")
        if _git(checkout, "rev-parse", "HEAD").decode().strip() != self.revision:
            raise FleetPlanInvalid("SKILL-2 checkout HEAD differs from Gitlink pin")
        if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
            raise FleetPlanInvalid("SKILL-2 Gitlink checkout has modified or untracked content")
        inside = self.path[len(self.gitlink) :].lstrip("/")
        return checkout, inside

    def verify(self, root: Path, captured: FrozenSourceTree | None = None) -> None:
        """Validate metadata and optionally the *original* AG-01 captured bytes.

        Git tree OIDs are compared to Git blob hashes of the frozen source bytes,
        not to a second filesystem read, preventing dirty/non-pinned resources
        from being falsely described as the committed Gitlink.
        """
        checkout, inside = self._context(root)
        tree_args = (
            ("ls-tree", "-r", "-z", "HEAD", "--", inside)
            if inside
            else ("ls-tree", "-r", "-z", "HEAD")
        )
        entries = _git(checkout, *tree_args).split(b"\x00")
        expected: dict[str, str] = {}
        for entry in entries:
            if not entry:
                continue
            match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})\t(.+)", entry)
            if match is None:
                raise FleetPlanInvalid("SKILL-2 Git tree contains unsupported source type")
            full_path = match.group(3).decode("utf-8", errors="strict")
            if inside:
                if not full_path.startswith(inside + "/"):
                    raise FleetPlanInvalid("SKILL-2 Git tree source escaped skill selection")
                full_path = full_path[len(inside) + 1 :]
            if not full_path or full_path in expected:
                raise FleetPlanInvalid("SKILL-2 duplicate or empty Git member")
            expected[full_path] = match.group(2).decode()
        if not expected or "SKILL.md" not in expected:
            raise FleetPlanInvalid("SKILL-2 pinned Git tree lacks SKILL.md")
        if captured is not None:
            observed = {
                m.path: hashlib.sha1(
                    b"blob " + str(len(m.data)).encode() + b"\x00" + m.data
                ).hexdigest()
                for m in captured.members
            }
            if observed != expected:
                raise FleetPlanInvalid("SKILL-2 captured resources differ from committed Git tree")


@dataclass(frozen=True, slots=True)
class FrozenSkillRegistry(Mapping[str, str]):
    """One bounded registry read; immutable selection plus Git metadata policy."""

    sha256: str
    aliases: Mapping[str, str]
    pins: Mapping[str, GitlinkSkillPin] = field(default_factory=lambda: MappingProxyType({}))

    def __getitem__(self, name: str) -> str:
        return self.aliases[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.aliases)

    def __len__(self) -> int:
        return len(self.aliases)


def read_goonskills(path: Path) -> FrozenSkillRegistry:
    """Read the one adjacent registry, refusing symlinks, duplicates or ambiguity."""
    if path.name != ".goonskills":
        raise FleetPlanInvalid("SKILL-1 registry filename must be .goonskills")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise FleetPlanInvalid("SKILL-1 cannot open regular .goonskills registry") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
            raise FleetPlanInvalid("SKILL-1 registry must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as file:
            raw = file.read(_MAX_BYTES + 1)
        after = os.fstat(fd)
        if (
            len(raw) > _MAX_BYTES
            or before.st_size != len(raw)
            or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise FleetPlanInvalid("SKILL-1 registry changed while being read or exceeds budget")
    finally:
        os.close(fd)
    try:
        parser = configparser.ConfigParser(
            interpolation=None, strict=True, default_section="DEFAULT"
        )
        parser.optionxform = str
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeError, configparser.Error) as exc:
        raise FleetPlanInvalid("SKILL-1 invalid .goonskills encoding or syntax") from exc
    if parser.defaults() or not parser.sections() or len(parser.sections()) > _MAX_SKILLS:
        raise FleetPlanInvalid("SKILL-1 registry has defaults, no skills or too many skills")
    aliases: dict[str, str] = {}
    pins: dict[str, GitlinkSkillPin] = {}
    for section in parser.sections():
        matched = _SECTION.fullmatch(section)
        if not matched or len(matched.group(1)) > 128 or not _NAME.fullmatch(matched.group(1)):
            raise FleetPlanInvalid("SKILL-1 invalid skill section name")
        name = matched.group(1)
        data = parser[section]
        source = data.get("source")
        value = data.get("path", "")
        if not _valid_path(value):
            raise FleetPlanInvalid("SKILL-1 unsafe local skill path")
        if source == "local" and set(data) == {"path", "source"}:
            aliases[name] = "local:" + value
        elif source == "gitlink" and set(data) == {"path", "source", "gitlink", "revision"}:
            link = data["gitlink"]
            commit = data["revision"]
            if (
                not _valid_path(link)
                or not _REVISION.fullmatch(commit)
                or not (value == link or value.startswith(link + "/"))
            ):
                raise FleetPlanInvalid("SKILL-2 invalid Gitlink path or immutable revision")
            pin = GitlinkSkillPin(path=value, gitlink=link, revision=commit)
            pins[name] = pin
            aliases[name] = pin.reference
        else:
            raise FleetPlanInvalid(
                "SKILL-1/2 requires source = local or pinned gitlink with exact fields"
            )
    return FrozenSkillRegistry(
        sha256=hashlib.sha256(raw).hexdigest(),
        aliases=MappingProxyType(aliases),
        pins=MappingProxyType(pins),
    )


def original_skill_aliases(
    plan_path: Path, explicit_aliases: Mapping[str, str] | None
) -> Mapping[str, str] | None:
    """Exactly one source of aliases; AG-01 still owns expansion and capture."""
    registry = plan_path.parent / ".goonskills"
    if not os.path.lexists(registry):
        return explicit_aliases
    if explicit_aliases is not None and len(explicit_aliases):
        raise FleetPlanInvalid("SKILL-1 .goonskills cannot be mixed with --skill-alias")
    return read_goonskills(registry)
