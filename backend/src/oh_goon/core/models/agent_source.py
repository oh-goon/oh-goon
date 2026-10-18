"""Immutable Agent source and Step-delivery contract.

The original resolver captures source bytes once. Authoring and execution share
these passive values and their canonical codec; this owner reads no source paths,
issues no admission, and performs no execution or journal effects.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from oh_goon.core.enforcer_lace_trust import canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
from oh_goon.core.product_identity import stable_id

MAX_SOURCE_ENTRIES = 4096
MAX_REPOSITORY_BYTES = 64 * 1024 * 1024
MAX_SKILL_BYTES = 16 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
# Upper bound on the base64 JSON delivery of the already bounded 80 MiB
# source trees; never accept an unbounded packet before parsing JSON.
MAX_PREWORK_PACKET_BYTES = 120 * 1024 * 1024
_PREWORK_PACKET_SCHEMA = "oh-goon.agent-step-prework-delivery/v1"


def _exact_dict(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise FleetPlanInvalid(f"AG-02B2A {label} has an invalid transport schema")
    return value


def _unique_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FleetPlanInvalid("AG-02B2A duplicate JSON transport key")
        result[key] = value
    return result


def _decode_packet_member(raw: object) -> "FrozenSourceMember":
    obj = _exact_dict(raw, {"path", "sha256", "data_b64"}, "source member")
    if not all(type(obj[k]) is str for k in obj):
        raise FleetPlanInvalid("AG-02B2A source member fields must be strings")
    encoded = obj["data_b64"]
    if len(encoded) > ((MAX_SOURCE_FILE_BYTES + 2) // 3) * 4:
        raise FleetPlanInvalid("AG-02B2A source member exceeds bound")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise FleetPlanInvalid("AG-02B2A invalid source member encoding") from exc
    if base64.b64encode(data).decode("ascii") != encoded:
        raise FleetPlanInvalid("AG-02B2A noncanonical source member encoding")
    return FrozenSourceMember(path=obj["path"], sha256=obj["sha256"], data=data)


def _decode_packet_tree(raw: object, expected_kind: str) -> "FrozenSourceTree":
    obj = _exact_dict(raw, {"kind", "content_identity", "members"}, "source tree")
    if obj["kind"] != expected_kind or type(obj["members"]) is not list:
        raise FleetPlanInvalid("AG-02B2A source tree kind or members invalid")
    if len(obj["members"]) > MAX_SOURCE_ENTRIES:
        raise FleetPlanInvalid("AG-02B2A source member count exceeds bound")
    tree = FrozenSourceTree(
        kind=expected_kind,
        content_identity=obj["content_identity"],
        members=tuple(_decode_packet_member(m) for m in obj["members"]),
    )
    tree.verify()
    return tree


@dataclass(frozen=True, slots=True)
class FrozenSourceMember:
    """Pre-admission bytes captured by the *same* authoritative source walk.

    No host path or open file descriptor is carried into execution. Transport
    and execution are intentionally unsupported until the original admitted
    Step owner receives an immutable source payload and validates its identity.
    """

    path: str
    sha256: str
    data: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or self.path.startswith("/")
            or "\\" in self.path
            or "\x00" in self.path
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
            or any(ord(char) < 32 for char in self.path)
        ):
            raise FleetPlanInvalid("AG-02 source capture member path unsafe")
        if type(self.data) is not bytes or len(self.data) > MAX_SOURCE_FILE_BYTES:
            raise FleetPlanInvalid("AG-02 source capture member exceeds byte bound")
        if type(self.sha256) is not str or len(self.sha256) != 64:
            raise FleetPlanInvalid("AG-02 source capture member digest invalid")
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise FleetPlanInvalid("AG-02 source capture member bytes do not match digest")


@dataclass(frozen=True, slots=True)
class FrozenSourceTree:
    kind: str
    content_identity: str
    members: tuple[FrozenSourceMember, ...]

    def verify(self) -> None:
        # Verify the original content commitment against the transportable bytes.
        if self.kind not in {"repository", "skill"} or not self.members:
            raise FleetPlanInvalid("AG-02 source capture tree kind or members invalid")
        if len(self.members) > MAX_SOURCE_ENTRIES:
            raise FleetPlanInvalid("AG-02 captured tree exceeds entry budget")
        paths = tuple(item.path for item in self.members)
        if paths != tuple(sorted(set(paths))):
            raise FleetPlanInvalid("AG-02 captured tree contains duplicate/unordered paths")
        total = sum(len(item.data) for item in self.members)
        if total > (MAX_REPOSITORY_BYTES if self.kind == "repository" else MAX_SKILL_BYTES):
            raise FleetPlanInvalid("AG-02 captured tree exceeds byte budget")
        manifest = []
        for item in self.members:
            digest = hashlib.sha256(item.data).hexdigest()
            if digest != item.sha256:
                raise FleetPlanInvalid("AG-02 captured member digest mismatch")
            manifest.append({"path": item.path, "sha256": digest, "bytes": len(item.data)})
        if stable_id("agent-source-closure-v1", {"members": manifest}) != self.content_identity:
            raise FleetPlanInvalid("AG-02 captured tree identity mismatch")


@dataclass(frozen=True, slots=True)
class FrozenAgentSourceCapture:
    """Resolver-owned prework evidence, NOT an admission or source transport."""

    plan_source_sha256: str
    plan_graph_identity: str
    acceptance_profile: str
    repository_reference: str
    resolved_skill_reference: str
    authored_skill_reference: str
    repository: FrozenSourceTree
    skill: FrozenSourceTree
    authority: str = "CAPTURED_PREWORK_NOT_ADMITTED"
    transported_immutable_snapshot: bool = False

    def verify(self) -> None:
        if (
            self.authority != "CAPTURED_PREWORK_NOT_ADMITTED"
            or self.transported_immutable_snapshot is not False
        ):
            raise FleetPlanInvalid(
                "AG-02 prework source cannot grant transport/admission authority"
            )
        if self.repository.kind != "repository" or self.skill.kind != "skill":
            raise FleetPlanInvalid("AG-02 source trees are in the wrong roles")
        self.repository.verify()
        self.skill.verify()
        if not any(item.path == "SKILL.md" for item in self.skill.members):
            raise FleetPlanInvalid("AG-02 captured skill does not contain SKILL.md")

    @property
    def capture_identity(self) -> str:
        """Domain-separated prework content/provenance identity, never a Run grant."""
        self.verify()
        return stable_id(
            "agent-source-prework-capture-v1",
            {
                "plan_source_sha256": self.plan_source_sha256,
                "plan_graph_identity": self.plan_graph_identity,
                "acceptance_profile": self.acceptance_profile,
                "repository_reference": self.repository_reference,
                "authored_skill_reference": self.authored_skill_reference,
                "resolved_skill_reference": self.resolved_skill_reference,
                "repository_content_identity": self.repository.content_identity,
                "skill_content_identity": self.skill.content_identity,
            },
        )


@dataclass(frozen=True, slots=True)
class FrozenAgentStepHandoff:
    """AG-02B original-Resolver prework *handoff*, NOT admitted transport.

    Exactly the AG-02A frozen bytes are retained; no path reacquisition,
    external harness invocation, or second execution authority is provided.
    """

    source: FrozenAgentSourceCapture = field(repr=False)
    owner_source_sha256: str
    step_id: str
    harness: str
    task: str = field(repr=False)
    timeout_seconds: float = 3600
    requested_effects: AgentStepRequestedEffects = field(default_factory=AgentStepRequestedEffects)
    authority: str = "PREWORK_NOT_ADMITTED"
    effect_permission: str = "NONE"

    def verify(self) -> None:
        self.source.verify()
        if (
            not self.owner_source_sha256
            or len(self.owner_source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.owner_source_sha256)
        ):
            raise FleetPlanInvalid("AG-02B original Step source SHA-256 is invalid")
        if (
            type(self.step_id) is not str
            or type(self.harness) is not str
            or type(self.task) is not str
            or type(self.timeout_seconds) not in {float, int}
            or not math.isfinite(self.timeout_seconds)
        ):
            raise FleetPlanInvalid("AG-02B Step handoff fields have invalid types")
        if not self.step_id or not self.harness or not self.task or self.timeout_seconds <= 0:
            raise FleetPlanInvalid("AG-02B Step handoff contract is incomplete")
        if type(self.requested_effects) is not AgentStepRequestedEffects:
            raise FleetPlanInvalid("SKILL-3B requested effects must use the typed Step contract")
        if self.authority != "PREWORK_NOT_ADMITTED" or self.effect_permission != "NONE":
            raise FleetPlanInvalid("AG-02B prework handoff cannot grant execution authority")

    @property
    def handoff_identity(self) -> str:
        self.verify()
        identity = {
            "capture_identity": self.source.capture_identity,
            "owner_source_sha256": self.owner_source_sha256,
            "step_id": self.step_id,
            "harness": self.harness,
            "task": self.task,
            "timeout_seconds": self.timeout_seconds,
        }
        # Historical AG-02 packets and identities remain byte-for-byte stable
        # when no additional requested effects were authored.
        if self.requested_effects != AgentStepRequestedEffects():
            identity["requested_effects"] = self.requested_effects.model_dump(
                mode="json", exclude_none=True
            )
        return stable_id("agent-step-prework-handoff-v1", identity)

    def encode_prework_delivery(self) -> bytes:
        """Lossless, canonical bytes from the ORIGINAL captured source, no re-read.

        This is deliberately NOT an admitted signed Run/Step transport. The
        eventual original Fleet/Enforcer transaction must bind these exact
        bytes to signer epoch, generation, claim, Step occurrence and journal.
        """
        self.verify()

        def tree_payload(tree: FrozenSourceTree) -> dict[str, object]:
            return {
                "kind": tree.kind,
                "content_identity": tree.content_identity,
                "members": [
                    {
                        "path": member.path,
                        "sha256": member.sha256,
                        "data_b64": base64.b64encode(member.data).decode("ascii"),
                    }
                    for member in tree.members
                ],
            }

        source = self.source
        step_payload = {
            "owner_source_sha256": self.owner_source_sha256,
            "step_id": self.step_id,
            "harness": self.harness,
            "task": self.task,
            "timeout_seconds": str(self.timeout_seconds),
        }
        if self.requested_effects != AgentStepRequestedEffects():
            step_payload["requested_effects"] = self.requested_effects.model_dump(
                mode="json", exclude_none=True
            )
        packet = canonical_bytes(
            {
                "schema": _PREWORK_PACKET_SCHEMA,
                "authority": "PREWORK_NOT_ADMITTED",
                "effect_permission": "NONE",
                "handoff_identity": self.handoff_identity,
                "step": step_payload,
                "source": {
                    "plan_source_sha256": source.plan_source_sha256,
                    "plan_graph_identity": source.plan_graph_identity,
                    "acceptance_profile": source.acceptance_profile,
                    "repository_reference": source.repository_reference,
                    "resolved_skill_reference": source.resolved_skill_reference,
                    "authored_skill_reference": source.authored_skill_reference,
                    "capture_identity": source.capture_identity,
                    "repository": tree_payload(source.repository),
                    "skill": tree_payload(source.skill),
                },
            }
        )
        if len(packet) > MAX_PREWORK_PACKET_BYTES:
            raise FleetPlanInvalid("AG-02B2A delivery exceeds transport byte budget")
        return packet

    @staticmethod
    def decode_prework_delivery(
        packet: bytes,
        *,
        expected_handoff_identity: str,
    ) -> "FrozenAgentStepHandoff":
        """Verify exact bytes without source-path access, signing or effects."""
        if (
            type(expected_handoff_identity) is not str
            or not expected_handoff_identity.startswith("agent-step-prework-handoff-v1-")
            or len(expected_handoff_identity) != len("agent-step-prework-handoff-v1-") + 64
        ):
            raise FleetPlanInvalid("AG-02B2A delivery requires original expected handoff identity")
        if type(packet) is not bytes or len(packet) > MAX_PREWORK_PACKET_BYTES:
            raise FleetPlanInvalid("AG-02B2A delivery must be bounded immutable bytes")
        try:
            raw = json.loads(
                packet.decode("utf-8"),
                object_pairs_hook=_unique_json_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    FleetPlanInvalid("AG-02B2A nonfinite JSON value")
                ),
            )
            obj = _exact_dict(
                raw,
                {"schema", "authority", "effect_permission", "handoff_identity", "step", "source"},
                "delivery",
            )
            if canonical_bytes(obj) != packet:
                raise FleetPlanInvalid("AG-02B2A delivery JSON is not canonical")
        except (UnicodeError, ValueError, TypeError) as exc:
            raise FleetPlanInvalid("AG-02B2A invalid delivery encoding") from exc
        if (
            obj["schema"] != _PREWORK_PACKET_SCHEMA
            or obj["authority"] != "PREWORK_NOT_ADMITTED"
            or obj["effect_permission"] != "NONE"
        ):
            raise FleetPlanInvalid("AG-02B2A delivery cannot claim admitted or effect authority")
        step_keys = {"owner_source_sha256", "step_id", "harness", "task", "timeout_seconds"}
        if type(obj["step"]) is not dict or set(obj["step"]) not in (
            step_keys,
            step_keys | {"requested_effects"},
        ):
            raise FleetPlanInvalid("SKILL-3B invalid requested-effects transport shape")
        raw_step = obj["step"]
        raw_source = _exact_dict(
            obj["source"],
            {
                "plan_source_sha256",
                "plan_graph_identity",
                "acceptance_profile",
                "repository_reference",
                "resolved_skill_reference",
                "authored_skill_reference",
                "capture_identity",
                "repository",
                "skill",
            },
            "source",
        )
        if any(type(raw_step[k]) is not str for k in step_keys) or any(
            type(raw_source[k]) is not str for k in raw_source if k not in {"repository", "skill"}
        ):
            raise FleetPlanInvalid("AG-02B2A delivery fields must be strings")
        try:
            timeout_text = raw_step["timeout_seconds"]
            timeout = int(timeout_text) if timeout_text.isdecimal() else float(timeout_text)
        except ValueError as exc:
            raise FleetPlanInvalid("AG-02B2A invalid Step timeout") from exc
        source = FrozenAgentSourceCapture(
            plan_source_sha256=raw_source["plan_source_sha256"],
            plan_graph_identity=raw_source["plan_graph_identity"],
            acceptance_profile=raw_source["acceptance_profile"],
            repository_reference=raw_source["repository_reference"],
            resolved_skill_reference=raw_source["resolved_skill_reference"],
            authored_skill_reference=raw_source["authored_skill_reference"],
            repository=_decode_packet_tree(raw_source["repository"], "repository"),
            skill=_decode_packet_tree(raw_source["skill"], "skill"),
        )
        try:
            requested_effects = (
                AgentStepRequestedEffects.model_validate(raw_step["requested_effects"])
                if "requested_effects" in raw_step
                else AgentStepRequestedEffects()
            )
        except (ValueError, TypeError) as exc:
            raise FleetPlanInvalid("SKILL-3B invalid typed requested effects") from exc
        handoff = FrozenAgentStepHandoff(
            source=source,
            owner_source_sha256=raw_step["owner_source_sha256"],
            step_id=raw_step["step_id"],
            harness=raw_step["harness"],
            task=raw_step["task"],
            timeout_seconds=timeout,
            requested_effects=requested_effects,
        )
        handoff.verify()
        if (
            source.capture_identity != raw_source["capture_identity"]
            or handoff.handoff_identity != obj["handoff_identity"]
            or handoff.handoff_identity != expected_handoff_identity
            or handoff.encode_prework_delivery() != packet
        ):
            raise FleetPlanInvalid("AG-02B2A delivery identity changed or was tampered")
        return handoff
