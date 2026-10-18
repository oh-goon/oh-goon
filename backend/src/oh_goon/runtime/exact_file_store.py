from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceNotFound
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
    require_expected_exact_file_revision,
    require_expected_revision,
)


class ExactFileStore(ABC):
    """Shared exact-file persistence skeleton for local user-owned YAML documents.

    Subclasses own domain validation and document parsing. This class owns the common
    path/list/read/write publication mechanics and delegates the stale-revision error
    vocabulary back to the domain store.
    """

    def __init__(
        self,
        root: Path,
        *,
        file_name: str,
        key_pattern: re.Pattern[str],
        label: str,
    ) -> None:
        self.root = root.expanduser().resolve()
        self._file_name = file_name
        self._key_pattern = key_pattern
        self._label = label
        if self.root.exists():
            ensure_private_directory(self.root)

    def path(self, key: str) -> Path:
        return self.root / self._normalize_key(key) / self._file_name

    def list_keys(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                child.name
                for child in self.root.iterdir()
                if not child.is_symlink()
                and child.is_dir()
                and self._key_pattern.fullmatch(child.name)
                and (child / self._file_name).is_file()
                and not (child / self._file_name).is_symlink()
            )
        )

    def _read_exact_bytes(
        self,
        key: str,
        *,
        expected_revision: str | None = None,
    ) -> tuple[str, bytes, str]:
        normalized = self._normalize_key(key)
        target = self.root / normalized / self._file_name
        if not target.exists():
            if expected_revision is not None:
                raise self._removed_stale(normalized)
            raise FleetPlanSourceNotFound(f"{self._label} does not exist: {normalized}")
        if target.is_symlink() or not target.is_file():
            raise self._invalid_exact_file(normalized)
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise FleetPlanInvalid(f"Cannot read {self._label} {normalized}: {exc}") from exc
        revision = hashlib.sha256(raw).hexdigest()
        if expected_revision is not None:
            require_expected_revision(
                revision,
                expected_revision,
                changed_error=lambda _current, _expected: self._changed_stale(normalized),
                removed_error=lambda: self._removed_stale(normalized),
            )
        return normalized, raw, revision

    def _commit_exact_text(
        self,
        key: str,
        content: str,
        *,
        expected_revision: str | None,
        newline: str | None = None,
    ) -> tuple[str, bytes, str]:
        normalized = self._normalize_key(key)
        ensure_private_directory(self.root)
        target = ensure_private_directory(self.root / normalized) / self._file_name
        raw = content.encode("utf-8")
        with exact_file_write_lock(target):
            self._require_target_revision(target, normalized, expected_revision)
            try:
                with prepared_text_file(target, content, newline=newline) as temporary:
                    self._require_target_revision(target, normalized, expected_revision)
                    replace_prepared_file(temporary, target)
            except OSError as exc:
                raise FleetPlanInvalid(
                    f"Cannot write {self._label} {normalized}: {exc}"
                ) from exc
        return normalized, raw, hashlib.sha256(raw).hexdigest()

    def _require_target_revision(
        self,
        target: Path,
        key: str,
        expected_revision: str | None,
    ) -> None:
        require_expected_exact_file_revision(
            target,
            expected_revision,
            invalid_error=lambda: self._invalid_exact_file(key),
            changed_error=lambda _current, _expected: self._changed_stale(key),
            removed_error=lambda: self._removed_stale(key),
        )

    @abstractmethod
    def _normalize_key(self, value: str) -> str: ...

    @abstractmethod
    def _invalid_exact_file(self, key: str) -> Exception: ...

    @abstractmethod
    def _changed_stale(self, key: str) -> Exception: ...

    @abstractmethod
    def _removed_stale(self, key: str) -> Exception: ...
