"""Repository evidence for the existing supervised, captured-source Agent tools."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.integrations.hashmarks.runtime_contract import (
    loaded_hashmarks_version,
    require_loaded_hashmarks_runtime,
)


@dataclass(frozen=True)
class PreparedAgentEvidence:
    root: Path
    handoff: FrozenAgentStepHandoff


@contextmanager
def prepare_agent_evidence(handoff: FrozenAgentStepHandoff) -> Iterator[PreparedAgentEvidence]:
    """Materialize captured bytes; the supervised tool owns native index work."""
    handoff.verify()
    with tempfile.TemporaryDirectory(prefix="oh-goon-agent-evidence-") as directory:
        root = Path(directory)
        for role, tree in (("repo", handoff.source.repository), ("skill", handoff.source.skill)):
            base = root / role
            base.mkdir(mode=0o700)
            for member in tree.members:
                path = base.joinpath(*member.path.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with path.open("xb") as stream:
                    stream.write(member.data)
                path.chmod(0o400)
        yield PreparedAgentEvidence(root, handoff)


def repository_observation(
    root: Path,
    *,
    tool: str,
    query: str | None,
    capture_identity: str,
) -> dict[str, Any]:
    from hashmarks import CodeMap

    state = root / "evidence-state"
    with CodeMap(root / "repo", state_dir=state, artifact_db=state / "artifacts.sqlite3") as native:
        with native.reads.session():
            envelope = {
                "producer": "Hashmarks",
                "producer_version": loaded_hashmarks_version(),
                "producer_implementation_identity": require_loaded_hashmarks_runtime(),
                "capture_identity": capture_identity,
                "path_prefix": "repo/",
                "repository_absence": "not-claimed",
            }
            if tool == "list_paths":
                paths = [item.rel for item in native.files.iter_files()]
                return {
                    **envelope,
                    "discovery_coverage": native.files.discovery_coverage(len(paths)),
                    "paths": ["repo/" + path for path in paths],
                }
            if tool != "search_text" or not query:
                raise ValueError("Repository evidence requires a supported tool and query")
            retrieval = cast(dict[str, Any], native.search.packet(query, limit=4))
            paths = list(dict.fromkeys(item["path"] for item in retrieval["results"]))
            return {
                **envelope,
                # Put producer bounds/freshness before possibly long source text so
                # bounded prompt disclosure retains the evidence's practical limits.
                "retrieval_state": {
                    key: value for key, value in retrieval.items() if key != "results"
                },
                "retrieval": retrieval,
                "source": native.source.scoped_occurrences(paths, query, limit=8, context_lines=1)
                if paths
                else None,
            }


def prompt_observation(stdout: str) -> str:
    """Present bounded native facts without cutting JSON or inventing match truth.

    The original tool packet stays in Step evidence. This disclosure is explicitly
    partial, preserves native scope/limits, and accounts for omitted rows.
    """
    from oh_goon.core.errors import FleetPlanInvalid

    try:
        packet = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise FleetPlanInvalid("Hashmarks tool did not return complete evidence JSON") from exc
    if not isinstance(packet, dict) or packet.get("producer") != "Hashmarks":
        raise FleetPlanInvalid("Agent repository tool returned an unexpected evidence envelope")
    projected = {
        key: packet[key]
        for key in ("producer", "capture_identity", "path_prefix", "repository_absence")
    }
    projected["projection_coverage"] = "partial"
    omitted = {"occurrences": 0, "members": 0, "paths": 0}
    projected["omitted"] = omitted
    source = packet.get("source")
    if isinstance(source, dict):
        fields = (
            "schema",
            "observation_scope",
            "generation",
            "freshness",
            "paths",
            "completeness",
            "truncation",
            "negative_evidence",
            "limits",
            "observation_identity",
        )
        source_projection = {key: source[key] for key in fields if key in source}
        source_projection["occurrences"] = [
            {
                key: row[key]
                for key in ("path", "member_revision", "line", "column", "context_excerpt")
                if key in row
            }
            for row in source["occurrences"]
        ]
        source_projection["members"] = list(source["member_observations"])
        projected["source"] = source_projection
        omitted["source_fields"] = len(
            set(source) - set(fields) - {"occurrences", "member_observations"}
        )
    else:
        projected["source"] = None
    if "paths" in packet:
        projected["paths"] = list(packet["paths"])
        projected["discovery_coverage"] = packet["discovery_coverage"]
    if "retrieval_state" in packet:
        projected["retrieval"] = {
            key: value
            for key, value in packet["retrieval_state"].items()
            if key in ("schema", "freshness", "truncated", "negative_evidence", "bound_reasons")
        }
    while True:
        encoded = json.dumps(projected, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) <= 2048:
            return encoded
        source_projection = projected.get("source") or {}
        for field, owner, count in (
            ("members", source_projection, "members"),
            ("occurrences", source_projection, "occurrences"),
            ("paths", projected, "paths"),
            ("paths", source_projection, "paths"),
        ):
            if owner.get(field):
                owner[field].pop()
                omitted[count] += 1
                break
        else:
            raise FleetPlanInvalid("Native repository evidence metadata exceeds the prompt budget")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", choices=("search_text", "list_paths"), required=True)
    parser.add_argument("--query")
    parser.add_argument("--capture-identity", required=True)
    args = parser.parse_args()
    try:
        result = repository_observation(
            Path.cwd(),
            tool=args.tool,
            query=args.query,
            capture_identity=args.capture_identity,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Hashmarks repository evidence unavailable: {exc}")
        return 65
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
