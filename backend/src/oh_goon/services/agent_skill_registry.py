"""One adjacent YAML registry: local aliases and grouped native Git sources."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from pydantic import Field, ValidationError, field_validator

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_lock_models import LockedSkillSource
from oh_goon.integrations.agent_skill_sources import PreparedSkill, prepare_skill_source
from oh_goon.integrations.fleet_plan_compilation import parse_fleet_yaml_document

REGISTRY_FILE_NAME = ".goonskills.yml"
_MAX_BYTES = 65536
_NAME = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*")


def _valid_path(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith("/")
        and str(PurePosixPath(value)) == value
        and all(part and not part.startswith(".") for part in value.split("/"))
        and all(c.isalnum() or c in "-_./" for c in value)
    )


class SkillSource(ImmutableModel):
    source: str = Field(min_length=1, max_length=2000)
    ref: str = Field(min_length=1, max_length=240)
    skills: tuple[str, ...] = Field(min_length=1, max_length=128)

    @field_validator("source", "ref")
    @classmethod
    def validate_git_argument(cls, value: str) -> str:
        if value.startswith("-") or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError("Git source and ref must be nonempty printable arguments")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if not (":" in value or Path(value).is_absolute()):
            raise ValueError("source must be a Git URL or absolute repository path")
        return value

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        if ":" in value or value.startswith("^") or "*" in value:
            raise ValueError("ref must select one branch, tag or commit, not a fetch refspec")
        return value

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or not all(_valid_path(value) for value in values):
            raise ValueError("skills must contain unique repository-relative directories")
        return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class FrozenSkillRegistry(Mapping[str, str]):
    """One registry read, with prepared source roots leased through byte capture."""

    aliases: Mapping[str, str]
    sources: Mapping[str, SkillSource] = field(default_factory=lambda: MappingProxyType({}))
    prepared: Mapping[str, PreparedSkill] = field(default_factory=lambda: MappingProxyType({}))
    pins: tuple[LockedSkillSource, ...] = ()

    def __getitem__(self, name: str) -> str:
        return self.aliases[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.aliases)

    def __len__(self) -> int:
        return len(self.aliases)


def read_goonskills(path: Path) -> FrozenSkillRegistry:
    """Parse exactly one bounded YAML file; native Git preparation is separate."""
    if path.name != REGISTRY_FILE_NAME:
        raise FleetPlanInvalid(f"Skill registry filename must be {REGISTRY_FILE_NAME}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise FleetPlanInvalid("Cannot open regular Skill registry") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
            raise FleetPlanInvalid("Skill registry must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as file:
            raw = file.read(_MAX_BYTES + 1)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or len(raw) > _MAX_BYTES
            or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise FleetPlanInvalid("Skill registry changed while being read or exceeds budget")
    finally:
        os.close(fd)
    try:
        data = parse_fleet_yaml_document(raw.decode("utf-8"), source=str(path)).payload
    except UnicodeError as exc:
        raise FleetPlanInvalid("Skill registry must be UTF-8 YAML") from exc
    if set(data) - {"local", "sources"} or not data:
        raise FleetPlanInvalid("Skill registry accepts local and sources mappings")
    local, sources = data.get("local", {}), data.get("sources", {})
    if not isinstance(local, dict) or not isinstance(sources, dict) or not (local or sources):
        raise FleetPlanInvalid("Skill registry needs a nonempty local or sources mapping")
    aliases: dict[str, str] = {}
    declared: dict[str, SkillSource] = {}
    for name, path_value in local.items():
        if not isinstance(path_value, str) or not _valid_path(path_value):
            raise FleetPlanInvalid("Skill registry has an unsafe local path")
        _add_alias(aliases, name, "local:" + path_value)
    for name, value in sorted(sources.items(), key=lambda item: str(item[0])):
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
        ):
            raise FleetPlanInvalid("Skill source names must be lowercase namespaces")
        try:
            source = SkillSource.model_validate(value)
        except ValidationError as exc:
            raise FleetPlanInvalid(f"Invalid Skill source {name!r}: {exc}") from exc
        declared[name] = source
        for skill in source.skills:
            _add_alias(
                aliases, name + "/" + PurePosixPath(skill).name, "source:" + name + ":" + skill
            )
    return FrozenSkillRegistry(
        aliases=MappingProxyType(aliases),
        sources=MappingProxyType(declared),
    )


def _add_alias(aliases: dict[str, str], name: object, reference: str) -> None:
    if not isinstance(name, str) or len(name) > 128 or not _NAME.fullmatch(name):
        raise FleetPlanInvalid("Skill registry has an invalid Skill name")
    if name in aliases:
        raise FleetPlanInvalid(
            f"Skill name {name!r} is ambiguous; choose distinct directories or namespaces"
        )
    if len(aliases) >= 128:
        raise FleetPlanInvalid("Skill registry exceeds 128 selected Skills")
    aliases[name] = reference


@contextmanager
def original_skill_aliases(
    plan_path: Path,
    explicit_aliases: Mapping[str, str] | None,
    *,
    locked_sources: tuple[LockedSkillSource, ...] | None = None,
    update_sources: bool = False,
    cancel_requested: Callable[[], bool] | None = None,
) -> Iterator[Mapping[str, str] | None]:
    """Prepare each collection once, preserving its lock pin across cache misses."""
    if isinstance(explicit_aliases, FrozenSkillRegistry):
        yield explicit_aliases
        return
    if locked_sources is None and not update_sources:
        from oh_goon.services.execution_lock_persistence import LOCK_FILE_NAME, load_execution_lock

        lock_path = plan_path.parent / LOCK_FILE_NAME
        if lock_path.exists():
            locked_sources = load_execution_lock(lock_path).skill_sources
    path = plan_path.parent / REGISTRY_FILE_NAME
    if not os.path.lexists(path):
        if locked_sources:
            raise FleetPlanInvalid(
                "goon.lock declares Skill sources but the Skill registry is missing"
            )
        yield explicit_aliases
        return
    if explicit_aliases:
        raise FleetPlanInvalid(f"{REGISTRY_FILE_NAME} cannot be mixed with --skill-alias")
    registry = read_goonskills(path)
    locked = {item.name: item for item in locked_sources or ()}
    if locked_sources is not None and not update_sources:
        if set(locked) != set(registry.sources) or len(locked) != len(locked_sources):
            raise FleetPlanInvalid(
                "Skill sources differ from goon.lock; run 'oh-goon lock --update-skills'"
            )
        for name, source in registry.sources.items():
            pin = locked[name]
            if (pin.source, pin.ref, pin.skills) != (source.source, source.ref, source.skills):
                raise FleetPlanInvalid(
                    f"Skill source {name!r} changed; run 'oh-goon lock --update-skills'"
                )
    prepared: dict[str, PreparedSkill] = {}
    pins: list[LockedSkillSource] = []
    with ExitStack() as stack:
        for name, source in registry.sources.items():
            try:
                root, commit = stack.enter_context(
                    prepare_skill_source(
                        source.source,
                        source.ref,
                        commit=locked[name].commit
                        if name in locked and not update_sources
                        else None,
                        cancel_requested=cancel_requested,
                    )
                )
            except FleetPlanInvalid as exc:
                raise FleetPlanInvalid(
                    f"Cannot prepare Skill source {name!r} at {source.ref!r}: {exc}"
                ) from exc
            pin = LockedSkillSource(
                name=name, source=source.source, ref=source.ref, commit=commit, skills=source.skills
            )
            pins.append(pin)
            for path_value in source.skills:
                alias = name + "/" + PurePosixPath(path_value).name
                prepared[alias] = PreparedSkill(root=root, path=path_value, source=pin)
        yield FrozenSkillRegistry(
            aliases=registry.aliases,
            sources=registry.sources,
            prepared=MappingProxyType(prepared),
            pins=tuple(pins),
        )
