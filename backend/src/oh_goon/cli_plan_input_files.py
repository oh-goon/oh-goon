"""Bounded caller-selected YAML inputs, not an authored Goon source or execution authority.

This component owns only CLI source capture. The canonical Plan binder remains
responsible for typing, unknown names, defaults and domain validation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.tokens import AliasToken, AnchorToken, TagToken

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.services.fleet_caller_file_freshness import (
    locate_caller_file as _path_within_caller_root,
)
from oh_goon.services.fleet_caller_file_freshness import (
    read_caller_file_bytes as _read_bytes,
)
from oh_goon.services.fleet_caller_file_freshness import (
    require_caller_file_fresh,
)

_MAX_BYTES = 65536
_MAX_NAMES = 64
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class CallerInputFileSnapshot:
    """Frozen path/digest/value observation; no downstream file reopen to select values."""

    relative_path: str
    sha256: str
    entries: tuple[tuple[str, PlanInputValue], ...]
    source_device: int
    source_inode: int
    caller_root: Path

    def values(self) -> dict[str, PlanInputValue]:
        return dict(self.entries)

    def authority(self) -> CallerInputFileIdentity:
        """Transport source identity, never any original caller values."""
        return CallerInputFileIdentity(
            caller_root=self.caller_root,
            relative_path=self.relative_path,
            sha256=self.sha256,
            source_device=self.source_device,
            source_inode=self.source_inode,
        )


class _UniqueMappingLoader(yaml.SafeLoader):
    """No implicit YAML overlay/merge or silent duplicate fields."""


def _mapping(loader: _UniqueMappingLoader, node: yaml.MappingNode, deep: bool = False):
    output: dict[str, object] = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if type(key) is not str or key == "<<":
            raise FleetPlanInvalid(
                "--inputs-file keys must be ordinary unique strings; merge keys are prohibited"
            )
        if key in output:
            raise FleetPlanInvalid(f"--inputs-file contains duplicate key {key!r}")
        output[key] = loader.construct_object(v, deep=deep)
    return output


_UniqueMappingLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_caller_input_file(path: Path, *, root: Path) -> CallerInputFileSnapshot:
    """Read one explicit caller source once; no CLI parser or Run preflight reads here."""
    selected, relative = _path_within_caller_root(path, root=root)
    before = selected.stat(follow_symlinks=False)
    data = _read_bytes(selected, expected=before)
    try:
        content = data.decode("utf-8", errors="strict")
        if any(
            isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(content)
        ):
            raise FleetPlanInvalid(
                "--inputs-file YAML aliases, anchors and custom tags are prohibited"
            )
        payload = yaml.load(content, Loader=_UniqueMappingLoader)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise FleetPlanInvalid("--inputs-file must contain valid UTF-8 safe YAML") from exc
    if (
        type(payload) is not dict
        or set(payload) != {"inputs"}
        or type(payload["inputs"]) is not dict
    ):
        raise FleetPlanInvalid("--inputs-file must contain exactly one top-level 'inputs' mapping")
    values = payload["inputs"]
    if len(values) > _MAX_NAMES:
        raise FleetPlanInvalid("--inputs-file exceeds the maximum of 64 caller input names")
    for name, value in values.items():
        if type(name) is not str or _NAME.fullmatch(name) is None:
            raise FleetPlanInvalid("--inputs-file contains an invalid input name")
        if type(value) not in (str, bool, int, type(None)):
            raise FleetPlanInvalid(
                f"--inputs-file input {name!r} must be a string, integer, boolean or null"
            )
        if isinstance(value, str) and "${{" in value:
            raise FleetPlanInvalid(
                f"--inputs-file input {name!r} cannot contain a value expression"
            )
    # Re-prove the same path, not a swapped inode; no read selects a second value.
    selected_after, relative_after = _path_within_caller_root(path, root=root)
    after = selected_after.stat(follow_symlinks=False)
    if (
        selected != selected_after
        or relative != relative_after
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise FleetPlanInvalid("--inputs-file changed its path during snapshot")
    return CallerInputFileSnapshot(
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        entries=tuple(values.items()),
        source_device=before.st_dev,
        source_inode=before.st_ino,
        caller_root=root.resolve(strict=True),
    )


def require_caller_input_file_unchanged(snapshot: CallerInputFileSnapshot, *, root: Path) -> None:
    """Compatibility entrypoint delegating the original Fleet freshness owner."""
    if root.resolve(strict=True) != snapshot.caller_root:
        raise FleetPlanInvalid("--inputs-file caller root differs from its frozen snapshot")
    require_caller_file_fresh(snapshot.authority())
