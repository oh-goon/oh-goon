"""The released producer observes one captured repository for the guided UI."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, cast

from oh_goon.integrations.hashmarks.repository_scope import repository_evidence_scope
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime


def observe_repository_exploration(
    root: Path, *, query: str, path: str | None = None
) -> dict[str, Any]:
    """Return intact native packets, retaining their actual freshness and omissions.

    The caller owns the bounded source snapshot. Native derived state belongs to
    this operation and never enters source or an independently persistent index.
    """
    if not query.strip() or len(query) > 512:
        raise ValueError("Repository exploration requires one bounded query")
    if path is not None:
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != path
            or "\\" in path
        ):
            raise ValueError("Repository exploration requires a contained canonical member path")
    producer = require_loaded_hashmarks_runtime()
    with TemporaryDirectory(prefix="oh-goon-catalog-evidence-") as directory:
        state = Path(directory)
        with repository_evidence_scope(
            root, state_dir=state / "index", artifact_db=state / "artifacts.sqlite3"
        ) as scope:
            native = scope.native
            synced = native.index.sync()
            with native.reads.session(expected_generation=synced.generation):
                if path is not None and native.files.visible_file(path) is None:
                    raise ValueError(
                        "Requested member is unavailable under repository evidence policy"
                    )
                orientation = cast(dict[str, Any], native.context.orient(max_areas=8))
                search = native.search.packet(query, limit=8)
                relationships = None
                relationship_status = "select-file" if path is None else "unsupported"
                if path is not None:
                    anchor = next(
                        (
                            hit
                            for hit in cast(list[dict[str, Any]], search["results"])
                            if hit["path"] == path
                            and hit.get("qualname")
                            and hit.get("lines") is not None
                        ),
                        None,
                    )
                    if anchor is not None:
                        try:
                            relationships = native.structure.locality(
                                path + "::" + anchor["qualname"],
                                max_depth=1,
                                call_limit_per_symbol=8,
                                ref_limit_per_symbol=8,
                                refresh=False,
                            )
                            relationship_status = "observed"
                        except ValueError:
                            # Missing exact structural capability is not permission
                            # to substitute an unbounded deps walk or infer a graph.
                            relationship_status = "unsupported"
                coverage = native.files.discovery_coverage(int(orientation["stats"]["files"]))
            scope.verify_policy()
    if require_loaded_hashmarks_runtime() != producer:
        raise ValueError("Repository exploration producer changed during observation")
    return {
        "producer_identity": producer,
        "orientation": orientation,
        "search": search,
        "relationships": relationships,
        "relationship_status": relationship_status,
        "coverage": coverage,
    }
