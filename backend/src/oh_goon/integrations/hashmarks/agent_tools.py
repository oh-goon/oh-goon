"""Repository evidence for the existing supervised, captured-source Agent tools."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from oh_goon.integrations.hashmarks.runtime_contract import (
    loaded_hashmarks_version,
    require_loaded_hashmarks_runtime,
)

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff


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
    state_directory: Path | None = None,
) -> dict[str, Any]:
    from hashmarks import CodeMap
    from hashmarks.evidence_presentation import present_repository_evidence

    state = state_directory if state_directory is not None else root / "evidence-state"
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
            if tool == "task_evidence":
                if not query:
                    raise ValueError("Task evidence requires the original task")
                evidence = native.tasks.evidence(
                    query,
                    limit=4,
                    per_role=1,
                    token_budget=512,
                    result_mode="initial",
                )
                return {
                    **envelope,
                    "evidence": evidence,
                    "presentation": present_repository_evidence(evidence, format="text"),
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
            retrieval = cast(dict[str, Any], native.search.packet(query, limit=2))
            paths = list(dict.fromkeys(item["path"] for item in retrieval["results"]))
            return {
                **envelope,
                # Put producer bounds/freshness before possibly long source text so
                # bounded prompt disclosure retains the evidence's practical limits.
                "retrieval_state": {
                    key: value for key, value in retrieval.items() if key != "results"
                },
                "retrieval": retrieval,
                "source": native.source.scoped_occurrences(paths, query, limit=4, context_lines=1)
                if paths
                else None,
            }


def task_evidence_prompt(
    stdout: str, *, expected_capture_identity: str, max_chars: int = 6000
) -> str:
    """Disclose the native initial presentation intact within a caller budget.

    The encoded packet remains in supervised Step evidence. This function adds
    only transport provenance; Hashmarks owns every displayed repository fact,
    its qualifications and its omission ledger. Oversize facts fail disclosure
    rather than becoming a silently clipped or consumer-selected observation.
    """
    from hashmarks.evidence_presentation import present_repository_evidence

    from oh_goon.core.errors import FleetPlanInvalid

    try:
        packet = json.loads(stdout)
        if not isinstance(packet, dict) or packet.get("producer") != "Hashmarks":
            raise ValueError("unexpected producer envelope")
        if packet.get("producer_implementation_identity") != require_loaded_hashmarks_runtime():
            raise ValueError("evidence producer differs from the loaded runtime")
        if (
            not expected_capture_identity
            or packet.get("capture_identity") != expected_capture_identity
        ):
            raise ValueError("evidence capture differs from the admitted source")
        evidence = packet["evidence"]
        if not isinstance(evidence, dict):
            raise ValueError("native initial evidence is missing")
        presentation = present_repository_evidence(evidence, format="text")
        if presentation.get("reference_space") != "decoded-initial-data":
            raise ValueError("native initial task evidence is required")
        if packet.get("presentation") != presentation:
            raise ValueError("presentation differs from the native evidence")
        projected = {
            key: packet[key]
            for key in (
                "producer",
                "producer_version",
                "producer_implementation_identity",
                "capture_identity",
                "path_prefix",
                "repository_absence",
            )
        }
        projected["evidence_packet_identity"] = evidence["evidence_packet_identity"]
        projected["source_evidence_identity"] = evidence["source_evidence_identity"]
        projected["presentation"] = presentation
        encoded = json.dumps(projected, ensure_ascii=True, separators=(",", ":"))
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid(f"Invalid Hashmarks task evidence: {exc}") from exc
    if len(encoded) > max_chars:
        raise FleetPlanInvalid("Native task evidence presentation exceeds the prompt budget")
    return encoded


def prompt_observation(
    stdout: str, *, expected_capture_identity: str, max_chars: int = 16000
) -> str:
    """Disclose canonical producer projections without reselecting their facts.

    Raw packets remain in supervised Step evidence. Hashmarks owns the retrieval
    and source projections, including their coverage and omission accounting.
    Only a plain discovered path list may lose trailing entries to fit transport;
    repository-evidence presentations either fit intact or fail disclosure.
    """
    from hashmarks.evidence_presentation import present_repository_evidence

    from oh_goon.core.errors import FleetPlanInvalid

    try:
        packet = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise FleetPlanInvalid("Hashmarks tool did not return complete evidence JSON") from exc
    if not isinstance(packet, dict) or packet.get("producer") != "Hashmarks":
        raise FleetPlanInvalid("Agent repository tool returned an unexpected evidence envelope")
    try:
        if packet.get("producer_implementation_identity") != require_loaded_hashmarks_runtime():
            raise ValueError("evidence producer differs from the loaded runtime")
        if (
            not expected_capture_identity
            or packet.get("capture_identity") != expected_capture_identity
        ):
            raise ValueError("evidence capture differs from the admitted source")
        projected = {
            key: packet[key]
            for key in (
                "producer",
                "producer_version",
                "producer_implementation_identity",
                "capture_identity",
                "path_prefix",
                "repository_absence",
            )
        }
        for kind in ("retrieval", "source"):
            native = packet.get(kind)
            if native is None:
                projected[kind] = None
                continue
            if not isinstance(native, dict):
                raise ValueError(f"native {kind} evidence is not an object")
            presentation = present_repository_evidence(native, format="compact")
            if presentation.get("supported") is not True:
                raise ValueError(f"native {kind} presentation is unsupported")
            projected[kind] = presentation
        if "paths" in packet:
            projected["paths"] = list(packet["paths"])
            projected["discovery_coverage"] = packet["discovery_coverage"]
            projected["omitted_paths"] = 0
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid(f"Invalid Hashmarks repository evidence: {exc}") from exc
    while True:
        encoded = json.dumps(projected, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) <= max_chars:
            return encoded
        if projected.get("paths"):
            projected["paths"].pop()
            projected["omitted_paths"] += 1
            continue
        raise FleetPlanInvalid("Native repository evidence presentation exceeds the prompt budget")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tool", choices=("task_evidence", "search_text", "list_paths"), required=True
    )
    query = parser.add_mutually_exclusive_group()
    query.add_argument("--query")
    query.add_argument("--query-file", type=Path)
    parser.add_argument("--capture-identity", required=True)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--state-directory", type=Path, default=None)
    args = parser.parse_args()
    try:
        query_text = args.query
        if args.query_file is not None:
            with args.query_file.open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError("Repository query exceeds 65536 UTF-8 bytes")
            query_text = data.decode("utf-8")
            if not query_text.strip() or "\x00" in query_text:
                raise ValueError("Repository query must be nonblank text without NUL")
        result = repository_observation(
            args.root if args.root is not None else Path.cwd(),
            tool=args.tool,
            query=query_text,
            capture_identity=args.capture_identity,
            state_directory=args.state_directory,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Hashmarks repository evidence unavailable: {exc}")
        return 65
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
