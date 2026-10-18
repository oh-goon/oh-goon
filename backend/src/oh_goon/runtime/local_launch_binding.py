from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from oh_goon.core.launch_manifest import LaunchIdentity, LaunchIntent, LaunchManifest
from oh_goon.core.path_authority import canonical_path, ensure_private_directory
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
)
from oh_goon.runtime.database import default_database_path
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

LOCAL_LAUNCH_BINDING_SCHEMA = "oh-goon.local-launch-binding/v1"


@dataclass(frozen=True, slots=True)
class LocalLaunchBinding:
    """Exact canonical workspace -> current launch owner binding."""

    workspace: str
    workspace_identity: str
    launch_id: str
    owner_generation: int
    intent_identity: str
    bound_at: datetime
    schema: str = LOCAL_LAUNCH_BINDING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LOCAL_LAUNCH_BINDING_SCHEMA:
            raise ValueError("unsupported local launch binding schema")
        if not self.workspace or not self.workspace_identity or not self.launch_id:
            raise ValueError("local launch binding requires workspace identity and launch identity")
        if self.owner_generation < 1:
            raise ValueError("local launch binding owner_generation must be >= 1")
        if not self.intent_identity.startswith("sha256:"):
            raise ValueError("local launch binding requires resolved launch-intent identity")
        if self.bound_at.tzinfo is None or self.bound_at.utcoffset() is None:
            raise ValueError("local launch binding bound_at must be timezone-aware")
        expected = workspace_identity(self.workspace)
        if self.workspace_identity != expected:
            raise ValueError("local launch binding workspace identity does not match workspace")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "workspace": self.workspace,
            "workspace_identity": self.workspace_identity,
            "launch_id": self.launch_id,
            "owner_generation": self.owner_generation,
            "intent_identity": self.intent_identity,
            "bound_at": self.bound_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class LocalLaunchBindingStore:
    """Deterministic exact binding store; never scans launches or chooses by recency."""

    root: Path

    @classmethod
    def from_database(cls, database_path: Path) -> LocalLaunchBindingStore:
        return cls(
            root=ApplicationStatePathLayout.from_database(database_path).local_launch_bindings
        )

    @classmethod
    def from_event_store(cls, event_store: LocalLaunchEventStore) -> LocalLaunchBindingStore:
        return cls(root=event_store.root.parent / "local-launch-bindings")

    @classmethod
    def configured(cls) -> LocalLaunchBindingStore:
        return cls.from_database(default_database_path())

    def bind(
        self,
        *,
        identity: LaunchIdentity,
        intent: LaunchIntent,
        event_store: LocalLaunchEventStore,
        timestamp: datetime,
    ) -> LocalLaunchBinding:
        workspace = str(canonical_path(identity.workspace))
        if workspace != identity.workspace:
            raise ValueError("launch identity workspace must already be canonical")
        binding = LocalLaunchBinding(
            workspace=workspace,
            workspace_identity=workspace_identity(workspace),
            launch_id=identity.launch_id,
            owner_generation=identity.owner_generation,
            intent_identity=intent.identity(),
            bound_at=timestamp,
        )
        target = self.path(workspace)
        ensure_private_directory(target.parent)
        with exact_file_write_lock(target):
            current = self._load_optional_locked(target)
            if current is not None:
                if _same_binding(current, binding):
                    return current
                # Never infer stale ownership from a missing/corrupt/old PID. Replacement is
                # permitted only after the exact previously-bound launch proves terminal.
                try:
                    current_manifest = event_store.manifest(current.launch_id)
                except (OSError, ValueError) as exc:
                    raise ValueError(
                        "current workspace launch owner cannot be proven terminal; binding replacement refused"
                    ) from exc
                try:
                    require_binding_matches_manifest(current, current_manifest)
                except ValueError as exc:
                    raise ValueError(
                        f"current workspace launch identity does not match binding; replacement refused: {exc}"
                    ) from exc
                if not current_manifest.lifecycle.is_terminal:
                    raise ValueError(
                        "current workspace launch is nonterminal; binding replacement refused"
                    )
            _publish_locked(target, binding.to_payload())
        return binding

    def resolve(self, workspace: Path | str) -> LocalLaunchBinding:
        binding = self.resolve_optional(workspace)
        if binding is None:
            raise ValueError("no current local launch binding for workspace")
        return binding

    def resolve_optional(self, workspace: Path | str) -> LocalLaunchBinding | None:
        canonical = str(canonical_path(workspace))
        target = self.path(canonical)
        with exact_file_write_lock(target):
            binding = self._load_optional_locked(target)
        if binding is not None and binding.workspace != canonical:
            raise ValueError("local launch binding belongs to another workspace")
        return binding

    def path(self, workspace: Path | str) -> Path:
        canonical = str(canonical_path(workspace))
        return self.root / f"{workspace_identity(canonical).removeprefix('sha256:')}.json"

    @staticmethod
    def _load_optional_locked(target: Path) -> LocalLaunchBinding | None:
        try:
            return _load_binding(target)
        except FileNotFoundError:
            return None

    @staticmethod
    def _load_locked(target: Path) -> LocalLaunchBinding:
        try:
            return _load_binding(target)
        except FileNotFoundError as exc:
            raise ValueError("no current local launch binding for workspace") from exc


def require_binding_matches_manifest(
    binding: LocalLaunchBinding,
    manifest: LaunchManifest,
) -> None:
    """Revalidate that one exact binding still names the same launch truth."""

    if manifest.identity.workspace != binding.workspace:
        raise ValueError("bound launch workspace does not match current workspace binding")
    if manifest.identity.owner_generation != binding.owner_generation:
        raise ValueError("bound launch owner generation does not match current workspace binding")
    if manifest.intent.identity() != binding.intent_identity:
        raise ValueError("bound launch intent identity does not match current workspace binding")


def workspace_identity(workspace: Path | str) -> str:
    canonical = str(canonical_path(workspace))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _same_binding(left: LocalLaunchBinding, right: LocalLaunchBinding) -> bool:
    return (
        left.workspace == right.workspace
        and left.launch_id == right.launch_id
        and left.owner_generation == right.owner_generation
        and left.intent_identity == right.intent_identity
    )


def _publish_locked(target: Path, payload: dict[str, object]) -> None:
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    with prepared_text_file(target, content) as temporary:
        replace_prepared_file(temporary, target)


def _load_binding(target: Path) -> LocalLaunchBinding:
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("local launch binding must be a JSON object")
    return _parse_binding(payload)


def _parse_binding(payload: Mapping[str, object]) -> LocalLaunchBinding:
    if payload.get("schema") != LOCAL_LAUNCH_BINDING_SCHEMA:
        raise ValueError("unsupported local launch binding schema")
    return LocalLaunchBinding(
        workspace=_string(payload, "workspace"),
        workspace_identity=_string(payload, "workspace_identity"),
        launch_id=_string(payload, "launch_id"),
        owner_generation=_integer(payload, "owner_generation"),
        intent_identity=_string(payload, "intent_identity"),
        bound_at=datetime.fromisoformat(_string(payload, "bound_at")),
        schema=_string(payload, "schema"),
    )


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"local launch binding {key} must be a non-empty string")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"local launch binding {key} must be an integer")
    return value
