from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.path_authority import ensure_private_directory, storage_component
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
)
from oh_goon.runtime.database import default_database_path

LOCAL_LAUNCH_ACCESS_SCHEMA = "oh-goon.local-launch-access/v1"


@dataclass(frozen=True, slots=True)
class LocalLaunchAccessPruneResult:
    removed: tuple[str, ...]
    refused: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LocalLaunchAccessStore:
    """Private exact-launch capability handoff, deliberately outside Launch Manifest.

    The store is not lifecycle truth and never discovers a launch. Callers must first
    resolve the exact workspace binding and prove the live owner through Launch state.
    It only maps one already-known launch id + owner generation to the capability that
    the launch owner actually supplied to its local API process.
    """

    root: Path

    @classmethod
    def from_database(cls, database_path: Path) -> LocalLaunchAccessStore:
        layout = ApplicationStatePathLayout.from_database(database_path)
        return cls(root=layout.local_launch_access)

    @classmethod
    def configured(cls) -> LocalLaunchAccessStore:
        return cls.from_database(default_database_path())

    def publish(self, *, launch_id: str, owner_generation: int, token: str) -> None:
        if not launch_id or not token:
            raise ValueError("local launch access requires launch_id and token")
        if owner_generation < 1:
            raise ValueError("local launch access owner_generation must be >= 1")
        target = self.path(launch_id)
        ensure_private_directory(target.parent)
        payload = {
            "schema": LOCAL_LAUNCH_ACCESS_SCHEMA,
            "launch_id": launch_id,
            "owner_generation": owner_generation,
            "token": token,
        }
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        with exact_file_write_lock(target):
            with prepared_text_file(target, content) as temporary:
                temporary.chmod(0o600)
                replace_prepared_file(temporary, target)

    def token(self, *, launch_id: str, owner_generation: int) -> str:
        target = self.path(launch_id)
        with exact_file_write_lock(target):
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ValueError("local launch access capability is unavailable") from exc
        if not isinstance(payload, dict) or payload.get("schema") != LOCAL_LAUNCH_ACCESS_SCHEMA:
            raise ValueError("unsupported local launch access capability schema")
        if payload.get("launch_id") != launch_id:
            raise ValueError("local launch access capability belongs to another launch")
        if payload.get("owner_generation") != owner_generation:
            raise ValueError("local launch access capability belongs to another owner generation")
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("local launch access capability token is invalid")
        return token

    def revoke(self, *, launch_id: str, owner_generation: int) -> bool:
        """Delete only the capability for one exact launch owner generation."""

        target = self.path(launch_id)
        with exact_file_write_lock(target):
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return False
            if not isinstance(payload, dict) or payload.get("schema") != LOCAL_LAUNCH_ACCESS_SCHEMA:
                raise ValueError("unsupported local launch access capability schema")
            if payload.get("launch_id") != launch_id:
                raise ValueError("local launch access capability belongs to another launch")
            if payload.get("owner_generation") != owner_generation:
                raise ValueError(
                    "local launch access capability belongs to another owner generation"
                )
            target.unlink()
        return True

    def prune_terminal(self, event_store) -> LocalLaunchAccessPruneResult:
        """Remove only capabilities whose exact launch generation is durably terminal.

        Launch state remains the lifecycle authority. Missing/invalid records, unknown
        launches, and nonterminal launches are refused rather than inferred stale.
        """

        if not self.root.exists():
            return LocalLaunchAccessPruneResult((), ())
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("local launch access root must be a real directory")
        removed: list[str] = []
        refused: list[str] = []
        for target in sorted(self.root.iterdir()):
            if target.is_symlink() or not target.is_file() or target.suffix != ".json":
                refused.append(target.name)
                continue
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                if (
                    not isinstance(payload, dict)
                    or payload.get("schema") != LOCAL_LAUNCH_ACCESS_SCHEMA
                ):
                    raise ValueError("invalid capability")
                launch_id = payload.get("launch_id")
                owner_generation = payload.get("owner_generation")
                if not isinstance(launch_id, str) or not launch_id:
                    raise ValueError("invalid launch id")
                if not isinstance(owner_generation, int) or isinstance(owner_generation, bool):
                    raise ValueError("invalid owner generation")
                manifest = event_store.manifest(launch_id)
                if manifest.identity.owner_generation != owner_generation:
                    raise ValueError("owner generation changed")
                if not manifest.lifecycle.is_terminal:
                    refused.append(target.name)
                    continue
                if self.revoke(launch_id=launch_id, owner_generation=owner_generation):
                    removed.append(target.name)
            except (OSError, ValueError, json.JSONDecodeError):
                refused.append(target.name)
        return LocalLaunchAccessPruneResult(tuple(removed), tuple(refused))

    def path(self, launch_id: str) -> Path:
        if not launch_id:
            raise ValueError("launch_id is required")
        return self.root / f"{storage_component(launch_id)}.json"
