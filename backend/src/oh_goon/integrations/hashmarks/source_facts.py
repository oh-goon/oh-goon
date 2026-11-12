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
    on_observation: Callable[[str, HashmarksSourceEvidence, dict[str, Any]], None] | None = None,
) -> dict[str, HashmarksSourceEvidence]:
    """Observe explicit members and optionally hand off their original native packet.

    The callback receives a call-owned mutable packet after its immutable evidence
    and captured bytes have been bound. Public evidence reads remain detached.
    A callback failure stops observation; it never selects another evidence route.
    """
    if isinstance(paths, str) or len(set(paths)) != len(paths):
        raise ValueError("Source bindings require distinct explicit member paths")
    if on_observation is not None and not callable(on_observation):
        raise ValueError("Source observation receiver must be callable")
    with _selected_scope(root, scope) as selected:
        result: dict[str, HashmarksSourceEvidence] = {}
        for path in paths:
            packet = cast(
                dict[str, Any],
                selected.native.source.bindings(path, limit=limit, max_bytes=max_bytes),
            )
            raw = None
            if packet["availability"] == "observed":
                member, raw = selected.native.source.member(
                    path, include_bytes=True, max_bytes=max_bytes
                )
                if member != packet["member"] or raw is None or len(raw) > max_bytes:
                    raise ValueError("Source bytes differ from their producer binding observation")
            evidence = _evidence(packet, selected, source_bytes=raw)
            if on_observation is not None:
                on_observation(path, evidence, packet)
                if require_loaded_hashmarks_runtime() != selected.producer:
                    raise ValueError("Source observation producer changed during observation")
                selected.verify_policy()
            result[path] = evidence
        return result


def capture_source_member(
    root: Path, path: str, *, scope: object | None = None, max_bytes: int = _SOURCE_BYTES
) -> HashmarksSourceEvidence:
    """Transfer one bounded original source member for consumer-owned input syntax."""
    with _selected_scope(root, scope) as selected:
        member, raw = selected.native.source.member(path, include_bytes=True, max_bytes=max_bytes)
        if member.get("state") != "known-present" or raw is None:
            raise HashmarksSourceInputError(
                "Selected source input is unavailable: " + path,
                producer_implementation_identity=selected.producer,
            )
        return _evidence(cast(dict[str, Any], member), selected, source_bytes=raw)


def verify_source_scope(root: Path, scope: object) -> None:
    """Verify the original source lifetime without rediscovery or new observations."""
    with _selected_scope(root, scope):
        pass


def observe_make_shell_declarations(
    root: Path,
    *,
    make_paths: Sequence[str] = (),
    shell_paths: Sequence[str] = (),
    scope: object | None = None,
    layout: Literal["groups", "file"] = "groups",
    selected_targets: Sequence[str] = (),
    include_source_bytes: bool = False,
    source_capture: HashmarksSourceEvidence | None = None,
) -> HashmarksSourceEvidence:
    """Keep native authored bindings separate from effective Make/shell execution."""
    from hashmarks.codemap import MakeShellDeclarations

    paths = (*make_paths, *shell_paths)
    if (include_source_bytes or source_capture is not None) and len(paths) != 1:
        raise ValueError("Source byte delivery requires one selected Make/shell member")
    provider = MakeShellDeclarations(
        make_paths=tuple(make_paths),
        shell_paths=tuple(shell_paths),
        layout=layout,
        selected_targets=tuple(selected_targets),
    )
    with _selected_scope(root, scope) as selected:
        selected.native.index.sync((*make_paths, *shell_paths))
        packet = cast(
            dict[str, Any],
            selected.native.declarations.operation(
                [], result_mode="discovery", providers=[provider]
            ),
        )
        raw = None
        if include_source_bytes or source_capture is not None:
            inputs = [
                row
                for provider in packet["providers"]
                for row in provider["inputs"]
                if row["path"] == paths[0] and row.get("observation_kind") == "content"
            ]
            if len(inputs) != 1:
                raise ValueError("Make/shell source byte authority is unavailable")
            if source_capture is not None:
                member, raw = source_capture.packet(), source_capture.source_bytes
                if (
                    source_capture.producer_implementation_identity != selected.producer
                    or member.get("path") != paths[0]
                ):
                    raise ValueError("Make/shell source capture has foreign authority")
            else:
                member, raw = selected.native.source.member(
                    paths[0],
                    include_bytes=True,
                    max_bytes=min(inputs[0].get("max_bytes", _SOURCE_BYTES), _SOURCE_BYTES),
                )
            if raw is None or member["member_revision"] != inputs[0]["member_revision"]:
                raise ValueError("Make/shell bytes differ from their producer observation")
        return _evidence(packet, selected, source_bytes=raw)


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


def observe_npm_project_declarations(
    root: Path,
    paths: Sequence[str] | None = None,
    *,
    fields: Sequence[str] = (),
    scope: object | None = None,
) -> HashmarksSourceEvidence:
    """Observe selected npm syntax, or admitted source manifest candidates.

    Discovery chooses source inputs only; missing or unsupported declarations do
    not become an empty complete package catalog. Extra selected JSON fields are
    opaque consumer configuration, never producer execution policy.
    """
    from hashmarks.codemap import NpmManifestDeclarations, RepositoryDeclarationProviderError

    if isinstance(fields, str):
        raise ValueError("Npm opaque field selection requires explicit names")
    if paths is not None and (isinstance(paths, str) or len(set(paths)) != len(paths)):
        raise ValueError("Npm declarations require distinct explicit member paths")
    with _selected_scope(root, scope) as selected:
        if paths is None:
            admitted: list[str] = []
            for member in selected.native.files.iter_files():
                if (
                    PurePosixPath(member.rel).name == "package.json"
                    and "node_modules" not in PurePosixPath(member.rel).parts
                    and member.visibility.value == "source"
                ):
                    admitted.append(member.rel)
                    if len(admitted) > 16:
                        raise ValueError(
                            "Npm source manifest inventory exceeds its observation bound"
                        )
            paths = tuple(sorted(admitted))
            if not paths:
                raise ValueError("Npm source context is unproven: no admitted manifest candidate")
        selected.native.index.sync(tuple(paths))
        try:
            packet = selected.native.declarations.operation(
                [],
                result_mode="discovery",
                providers=[NpmManifestDeclarations(tuple(paths), fields=tuple(fields))],
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
    selected_targets: Sequence[str] = (),
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
            selected_targets=selected_targets,
        )


def observe_python_project_declarations_text(text: str) -> HashmarksSourceEvidence:
    """Observe captured authored metadata without ambient project discovery."""
    with _captured_text(text, "pyproject.toml") as root:
        return observe_python_project_declarations(root)
