"""Source facts from the released producer, with no consumer syntax fallback."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast

from oh_goon.integrations.hashmarks.repository_scope import repository_evidence_scope
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

if TYPE_CHECKING:
    from hashmarks import CodeMap

_SOURCE_BYTES = 8_388_608


class HashmarksSourceInputError(ValueError):
    """A native declaration input failure bound to its loaded producer."""

    def __init__(self, message: str, *, producer_implementation_identity: str) -> None:
        super().__init__(message)
        self.producer_implementation_identity = producer_implementation_identity


@dataclass(frozen=True, slots=True)
class HashmarksSourceEvidence:
    """An intact producer packet and the loaded implementation that observed it.

    Availability, syntax identity, source revision, uncertainty and omissions
    remain in the packet. This binding does not qualify unknown producer facts.
    """

    producer_implementation_identity: str
    canonical_packet_json: str
    source_bytes: bytes | None = None

    def packet(self) -> dict[str, Any]:
        """Return a detached packet without a consumer semantic projection."""
        return cast(dict[str, Any], json.loads(self.canonical_packet_json))


@dataclass(slots=True)
class _SourceScope:
    root: Path
    native: CodeMap
    producer: str
    verify_policy: Callable[[], None]
    active: bool = True


@contextmanager
def hashmarks_source_scope(root: Path) -> Iterator[object]:
    """Own one native lifetime for explicit member observations.

    The yielded token grants only the named observation operations below. Native
    Source supports unindexed member reads; no repository indexing read session
    is required or claimed. Hashmarks owns admission, parsing and freshness.
    """
    producer = require_loaded_hashmarks_runtime()
    with tempfile.TemporaryDirectory(prefix="oh-goon-source-facts-") as directory:
        state = Path(directory)
        with repository_evidence_scope(
            root, state_dir=state, artifact_db=state / "artifacts.sqlite3"
        ) as repository_scope:
            scope = _SourceScope(
                root.resolve(), repository_scope.native, producer, repository_scope.verify_policy
            )
            try:
                yield scope
            finally:
                scope.active = False


@contextmanager
def _selected_scope(root: Path, scope: object | None) -> Iterator[_SourceScope]:
    if scope is None:
        with hashmarks_source_scope(root) as selected:
            yield cast(_SourceScope, selected)
        return
    if not isinstance(scope, _SourceScope) or not scope.active or scope.root != root.resolve():
        raise ValueError("Source observation requires its active exact-root lifetime")
    if require_loaded_hashmarks_runtime() != scope.producer:
        raise ValueError("Source observation producer changed during its lifetime")
    scope.verify_policy()
    yield scope
    scope.verify_policy()


def _evidence(
    packet: dict[str, Any], scope: _SourceScope, *, source_bytes: bytes | None = None
) -> HashmarksSourceEvidence:
    if require_loaded_hashmarks_runtime() != scope.producer:
        raise ValueError("Source observation producer changed during observation")
    scope.verify_policy()
    return HashmarksSourceEvidence(
        producer_implementation_identity=scope.producer,
        canonical_packet_json=json.dumps(
            packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
        source_bytes=source_bytes,
    )


def observe_source_bindings(
    root: Path,
    paths: Sequence[str],
    *,
    scope: object | None = None,
    limit: int = 100_000,
    max_bytes: int = _SOURCE_BYTES,
) -> dict[str, HashmarksSourceEvidence]:
    """Observe explicit members through the producer's canonical source owner."""
    if isinstance(paths, str) or len(set(paths)) != len(paths):
        raise ValueError("Source bindings require distinct explicit member paths")
    with _selected_scope(root, scope) as selected:
        result: dict[str, HashmarksSourceEvidence] = {}
        for path in paths:
            packet = cast(
                dict[str, Any],
                selected.native.source.bindings(path, limit=limit, max_bytes=max_bytes),
            )
            raw = None
            if packet["availability"] == "observed":
                member, raw = selected.native.source.member(path, include_bytes=True)
                if member != packet["member"] or raw is None or len(raw) > max_bytes:
                    raise ValueError("Source bytes differ from their producer binding observation")
            result[path] = _evidence(packet, selected, source_bytes=raw)
        return result


def observe_make_shell_declarations(
    root: Path,
    *,
    make_paths: Sequence[str] = (),
    shell_paths: Sequence[str] = (),
    scope: object | None = None,
    layout: Literal["groups", "file"] = "groups",
) -> HashmarksSourceEvidence:
    """Keep native authored bindings separate from effective Make/shell execution."""
    from hashmarks.codemap import MakeShellDeclarations

    provider = MakeShellDeclarations(
        make_paths=tuple(make_paths), shell_paths=tuple(shell_paths), layout=layout
    )
    with _selected_scope(root, scope) as selected:
        selected.native.index.sync((*make_paths, *shell_paths))
        packet = selected.native.declarations.operation(
            [], result_mode="discovery", providers=[provider]
        )
        return _evidence(cast(dict[str, Any], packet), selected)


def observe_python_project_declarations(
    root: Path, *, scope: object | None = None
) -> HashmarksSourceEvidence:
    """Observe authored root metadata without selecting a native environment."""
    from hashmarks.codemap import PythonUvManifestDeclarations, RepositoryDeclarationProviderError

    with _selected_scope(root, scope) as selected:
        # Prepare only the caller-selected manifest through the native owner.
        # This avoids cold whole-repository bootstrap for one authored input;
        # the packet still reports repository-wide freshness as unknown.
        selected.native.index.sync(("pyproject.toml",))
        try:
            packet = selected.native.declarations.operation(
                [],
                result_mode="discovery",
                providers=[PythonUvManifestDeclarations(paths=("pyproject.toml",))],
            )
        except RepositoryDeclarationProviderError as error:
            raise HashmarksSourceInputError(
                str(error), producer_implementation_identity=selected.producer
            ) from error
        return _evidence(cast(dict[str, Any], packet), selected)


@contextmanager
def _captured_text(text: str, path: str) -> Iterator[Path]:
    relative = PurePosixPath(path)
    if (
        not path
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != path
        or "\\" in path
    ):
        raise ValueError("Captured source requires a canonical relative member path")
    data = text.encode("utf-8")
    if len(data) > _SOURCE_BYTES:
        raise ValueError("Captured source exceeds its transport byte budget")
    with tempfile.TemporaryDirectory(prefix="oh-goon-source-capture-") as directory:
        root = Path(directory)
        member = root / relative
        member.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with member.open("xb") as stream:
            stream.write(data)
        member.chmod(0o400)
        yield root


def observe_source_bindings_text(text: str, *, path: str) -> HashmarksSourceEvidence:
    """Observe exact captured fixture bytes through the same native source owner."""
    with _captured_text(text, path) as root:
        return observe_source_bindings(root, (path,))[path]


def observe_make_shell_declarations_text(
    text: str,
    *,
    path: str,
    language: Literal["make", "shell"],
    layout: Literal["groups", "file"] = "groups",
) -> HashmarksSourceEvidence:
    """Capture authored text; the producer alone interprets its selected language."""
    if language not in {"make", "shell"}:
        raise ValueError("Make/shell source requires its explicit language")
    with _captured_text(text, path) as root:
        return observe_make_shell_declarations(
            root,
            make_paths=(path,) if language == "make" else (),
            shell_paths=(path,) if language == "shell" else (),
            layout=layout,
        )


def observe_python_project_declarations_text(text: str) -> HashmarksSourceEvidence:
    """Observe captured authored metadata without ambient project discovery."""
    with _captured_text(text, "pyproject.toml") as root:
        return observe_python_project_declarations(root)
