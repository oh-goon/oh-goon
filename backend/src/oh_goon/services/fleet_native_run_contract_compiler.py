"""M172: compile a Fleet Run candidate from *native owner observations*.

No signing, credential selection, alternative Plan resolver, or admission write.
The opaque policy/envelope half MUST be pre-provisioned by the deployment owner;
this compiler never fabricates a default. Six evidence identities, Run identity,
Plan lock and executor generation are derived solely from their native owners.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping, cast

from pydantic import ValidationError

from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS
from oh_goon.core.enforcer_lace_trust import (
    CONTRACT_KEYS,
    AdmittedRunContract,
    TrustProtocolError,
    canonical_bytes,
    digest,
)
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.native_issuer import (
    enforcer_lace_backend_capability_issuer as capability_owner,
)
from oh_goon.native_issuer import (
    enforcer_lace_captured_run_issuer as run_owner,
)
from oh_goon.native_issuer import (
    enforcer_lace_execution_environment_issuer as environment_owner,
)
from oh_goon.native_issuer import (
    enforcer_lace_native_lock_issuer as lock_owner,
)
from oh_goon.native_issuer import (
    enforcer_lace_native_tool_issuer as tool_owner,
)
from oh_goon.native_issuer import (
    enforcer_lace_repository_snapshot_issuer as repository_native,
)
from oh_goon.native_issuer import (
    enforcer_lace_run_source_issuer as source_owner,
)
from oh_goon.native_issuer.enforcer_lace_prework_capture import PreworkRunCapture

DERIVED_FIELDS = AUTHORITY_FIELDS | frozenset(
    {
        "run_id",
        "resolved_plan_identity",
        "executor_generation",
    }
)
DEPLOYMENT_POLICY_FIELDS = CONTRACT_KEYS - DERIVED_FIELDS


@dataclass(frozen=True, slots=True)
class NativeAgentEffectCeilings:
    """Separately enrolled, immutable deployment limits, never Agent authoring.

    The enrollment owner must provision this object independently of the Plan,
    AG-02 packet and any Step-supplied envelope. It is policy data, not a
    signing authority, resource reservation or effect grant.
    """

    encoded: bytes

    @classmethod
    def freeze(cls, ceilings: Mapping[str, object]) -> "NativeAgentEffectCeilings":
        if (
            type(ceilings) is not dict
            or not ceilings
            or any(type(step) is not str or not step for step in ceilings)
        ):
            raise TrustProtocolError("explicit deployment-owned Agent Step limits required")
        for limit in ceilings.values():
            if type(limit) is not dict:
                raise TrustProtocolError("Agent Step limit must be a typed plain mapping")
            try:
                parsed = AgentStepRequestedEffects.model_validate(limit)
            except (ValidationError, TypeError, ValueError) as exc:
                raise TrustProtocolError("invalid deployment Agent effects ceiling") from exc
            if parsed.model_dump(mode="json") != limit:
                raise TrustProtocolError("deployment Agent effects ceiling must be canonical")
        return cls(canonical_bytes(ceilings))

    def read(self) -> dict[str, object]:
        try:
            values = json.loads(self.encoded)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise TrustProtocolError("invalid frozen Agent policy bytes") from exc
        if NativeAgentEffectCeilings.freeze(values).encoded != self.encoded:
            raise TrustProtocolError("noncanonical frozen Agent policy")
        # freeze() above validates the canonical plain JSON policy map.
        return cast(dict[str, object], values)


_AGENT_POLICY_MAX_BYTES = 65536


def _native_agent_policy_principal() -> int:
    # Reuse the original Fleet provisioner's Linux caller-custody observation.
    # A root/privileged signer can read and overwrite every DAC-protected
    # deployment file and cannot prove independent policy ownership.
    from oh_goon.services.fleet_prework_provisioner import _native_unprivileged_signer_identity

    _native_unprivileged_signer_identity()
    uid = os.geteuid()
    if uid == 0:
        raise TrustProtocolError("privileged signer cannot prove independent Agent policy")
    return uid


def _require_admin_policy_ancestry(
    path: Path,
    *,
    owner_uid: int,
    signer_uid: int,
) -> tuple[tuple[int, ...], ...]:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path == Path("/")
        or ".." in path.parts
        or os.path.abspath(path) != str(path)
        or type(owner_uid) is not int
        or owner_uid < 0
        or owner_uid == signer_uid
    ):
        raise TrustProtocolError("independent absolute deployment Agent policy path required")
    identities = []
    # Every component, including /, must be an immutable-to-signer directory.
    for ancestor in (*reversed(path.parent.parents), path.parent):
        try:
            item = os.stat(ancestor, follow_symlinks=False)
        except OSError as exc:
            raise TrustProtocolError("deployment Agent policy ancestry unavailable") from exc
        if (
            not stat.S_ISDIR(item.st_mode)
            or item.st_uid == signer_uid
            or item.st_uid not in (0, owner_uid)
            or item.st_mode & 0o022
        ):
            raise TrustProtocolError("deployment Agent policy ancestry is signer-mutable")
        identities.append(
            (
                item.st_dev,
                item.st_ino,
                item.st_uid,
                item.st_mode,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )
        )
    return tuple(identities)


def _read_admin_agent_policy(path: Path, *, owner_uid: int) -> tuple[bytes, tuple[int, ...]]:
    signer_uid = _native_agent_policy_principal()
    before = _require_admin_policy_ancestry(path, owner_uid=owner_uid, signer_uid=signer_uid)
    if any(not hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")):
        raise TrustProtocolError("native deployment Agent policy descriptor safety unavailable")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        start = os.fstat(fd)
        if (
            not stat.S_ISREG(start.st_mode)
            or start.st_uid != owner_uid
            or start.st_uid == signer_uid
            or start.st_nlink != 1
            or start.st_mode & 0o022
            or start.st_size > _AGENT_POLICY_MAX_BYTES
        ):
            raise TrustProtocolError("deployment Agent policy file lacks independent custody")
        data = os.read(fd, _AGENT_POLICY_MAX_BYTES + 1)
        end = os.fstat(fd)
        attrs = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(data) > _AGENT_POLICY_MAX_BYTES
            or any(getattr(start, attr) != getattr(end, attr) for attr in attrs)
            or len(data) != end.st_size
        ):
            raise TrustProtocolError("deployment Agent policy changed during fd-anchored read")
        leaf = tuple(getattr(end, attr) for attr in attrs)
    except OSError as exc:
        raise TrustProtocolError("independent deployment Agent policy file unavailable") from exc
    finally:
        if fd is not None:
            os.close(fd)
    after = _require_admin_policy_ancestry(path, owner_uid=owner_uid, signer_uid=signer_uid)
    if before != after or _native_agent_policy_principal() != signer_uid:
        raise TrustProtocolError("deployment Agent policy source changed during read")
    return data, leaf


@dataclass(frozen=True, slots=True)
class DeploymentAgentPolicySource:
    """Pinned externally controlled deployment document, never Plan data.

    It is read again at the original native contract cut. Its identity is
    evidence about the *source of a ceiling*, not an authorization/grant.
    """

    path: Path
    owner_uid: int
    file_identity: tuple[int, ...]
    encoded: bytes

    @classmethod
    def enroll(cls, path: Path, *, owner_uid: int) -> "DeploymentAgentPolicySource":
        if not isinstance(path, Path) or not path.is_absolute():
            raise TrustProtocolError("explicit absolute deployment Agent policy path required")
        encoded, identity = _read_admin_agent_policy(path, owner_uid=owner_uid)
        try:
            values = json.loads(encoded)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise TrustProtocolError("invalid deployment Agent policy document") from exc
        if NativeAgentEffectCeilings.freeze(values).encoded != encoded:
            raise TrustProtocolError("deployment Agent policy document is not canonical")
        return cls(path, owner_uid, identity, encoded)

    @property
    def identity(self) -> str:
        return "sha256:" + sha256(self.encoded).hexdigest()

    def require_matches(self, ceilings: NativeAgentEffectCeilings) -> None:
        if (
            type(self) is not DeploymentAgentPolicySource
            or type(ceilings) is not NativeAgentEffectCeilings
        ):
            raise TrustProtocolError("original deployment Agent policy source required")
        encoded, identity = _read_admin_agent_policy(self.path, owner_uid=self.owner_uid)
        if identity != self.file_identity or encoded != self.encoded or encoded != ceilings.encoded:
            raise TrustProtocolError(
                "deployment Agent policy document changed or differs from enrolled ceiling"
            )
        ceilings.read()


@dataclass(frozen=True, slots=True)
class DeploymentRunContractPolicy:
    """Explicit policy/envelopes from the deployment owner, not from the Plan.

    Freeze immediately: mutating a caller's nested mapping must never mutate
    the original policy that is transported to the native compiler.
    """

    encoded: bytes
    agent_effect_policy: NativeAgentEffectCeilings | None = None
    agent_policy_source: DeploymentAgentPolicySource | None = None

    @classmethod
    def freeze(
        cls,
        fields: Mapping[str, object],
        *,
        agent_effect_policy: NativeAgentEffectCeilings | None = None,
        agent_policy_source: DeploymentAgentPolicySource | None = None,
    ) -> "DeploymentRunContractPolicy":
        if type(fields) is not dict or set(fields) != DEPLOYMENT_POLICY_FIELDS:
            raise TrustProtocolError("exact deployment-owned Run policy fields required")
        if type(fields["step_envelopes"]) is not dict:
            raise TrustProtocolError("deployment Step envelopes must be structured")
        if (
            agent_effect_policy is not None
            and type(agent_effect_policy) is not NativeAgentEffectCeilings
        ):
            raise TrustProtocolError("independently enrolled Agent ceilings required")
        if any(
            type(step) is not dict or "agent_effect_limits" in step
            for step in fields["step_envelopes"].values()
        ):
            raise TrustProtocolError("Agent effect limits cannot come from Step envelopes")
        encoded = canonical_bytes(fields)
        if agent_effect_policy is not None:
            agent_effect_policy.read()
        if agent_policy_source is not None:
            if (
                agent_effect_policy is None
                or type(agent_policy_source) is not DeploymentAgentPolicySource
            ):
                raise TrustProtocolError("deployment Agent source requires typed ceilings")
            agent_policy_source.require_matches(agent_effect_policy)
        return cls(encoded, agent_effect_policy, agent_policy_source)

    def read(self) -> dict[str, object]:
        try:
            fields = json.loads(self.encoded)
            if canonical_bytes(fields) != self.encoded or set(fields) != DEPLOYMENT_POLICY_FIELDS:
                raise TrustProtocolError("deployment Run policy is noncanonical or incomplete")
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise TrustProtocolError("invalid frozen deployment Run policy") from exc
        if type(fields["step_envelopes"]) is not dict:
            raise TrustProtocolError("invalid frozen deployment Step envelopes")
        if any(
            type(step) is not dict or "agent_effect_limits" in step
            for step in fields["step_envelopes"].values()
        ):
            raise TrustProtocolError("Step envelope must not self-authorize Agent effects")
        if self.agent_effect_policy is not None:
            if type(self.agent_effect_policy) is not NativeAgentEffectCeilings:
                raise TrustProtocolError("invalid independently enrolled Agent effect policy")
            self.agent_effect_policy.read()
        if self.agent_policy_source is not None:
            if (
                self.agent_effect_policy is None
                or type(self.agent_policy_source) is not DeploymentAgentPolicySource
            ):
                raise TrustProtocolError("invalid external deployment Agent policy source")
            self.agent_policy_source.require_matches(self.agent_effect_policy)
        # Canonical JSON and exact deployment fields were verified above.
        return cast(dict[str, object], fields)


def _apply_native_agent_effect_policy(
    deployment: dict[str, object],
    policy: NativeAgentEffectCeilings | None,
    source: DeploymentAgentPolicySource | None,
) -> None:
    """One original compiler cut inserts policy, never copies author demand.

    No grant or execution permission is created. The original journal remains
    the only semantic Enforcer owner of request-versus-limit comparison.
    """
    # DeploymentRunContractPolicy.read checks this map and each envelope.
    steps = cast(dict[str, dict[str, object]], deployment["step_envelopes"])
    agent_steps = {
        step_id for step_id, envelope in steps.items() if envelope.get("workload_kind") == "agent"
    }
    if not agent_steps:
        if policy is not None:
            raise TrustProtocolError("Agent policy supplied for non-Agent Plan")
        return
    if policy is None:
        raise TrustProtocolError("Agent Step lacks separately enrolled native effect policy")
    if source is None:
        raise TrustProtocolError("Agent Step lacks independent deployment policy source custody")
    source.require_matches(policy)
    limits = policy.read()
    if set(limits) != agent_steps:
        raise TrustProtocolError("native Agent policy does not cover exact active Agent Steps")
    from oh_goon.runtime.enforcer_lace_journal import _independently_scoped_agent_effects

    for step_id in sorted(agent_steps):
        envelope = steps[step_id]
        demand = envelope.get("agent_requested_effects")
        if demand is not None and type(demand) is not dict:
            raise TrustProtocolError("original Agent demand must be a canonical mapping")
        candidate = {**envelope, "agent_effect_limits": limits[step_id]}
        # Reuse exactly the original journal's conservative narrowing rules:
        # no alternative Agent policy interpreter in native compilation.
        _independently_scoped_agent_effects(demand, candidate)
        steps[step_id] = candidate


def _owner_observation(
    prework: PreworkRunCapture,
    authority: StrictHostPathAuthority,
    control_state_root: Path | None,
    *,
    check_uv: bool,
) -> dict[str, object]:
    admitted, revision = prework.read(journal=prework.journal, run_id=prework.run_id)
    if not isinstance(admitted.target, FleetPlanTarget):
        raise TrustProtocolError("original Fleet Run must select one resolved Plan")
    selected = tuple(item.repository_id for item in admitted.repository_admissions)
    roots = {item.repository_id: item.path for item in admitted.targets}
    if (
        len(selected) < 2
        or selected != tuple(sorted(set(selected)))
        or len(roots) != len(selected)
        or set(roots) != set(selected)
    ):
        raise TrustProtocolError("original Fleet repository membership is not canonical")
    # Reuse the existing source issuer's exact ordered-admission projection and
    # Hashmarks-backed native snapshot observer; no replacement source index.
    source_identity = source_owner.admitted_run_repository_source_identity(
        admissions=admitted.repository_admissions,
        selected_repository_ids=selected,
    )
    for item in admitted.repository_admissions:
        root = roots[item.repository_id]
        if (
            not root.is_absolute()
            or root.is_symlink()
            or not root.is_dir()
            or root.resolve() != root
        ):
            raise TrustProtocolError("unsafe source root in original Fleet capture")
        try:
            subject = repository_native.repository_subject_instance_id(root)
            snapshot = repository_native.source_snapshot_identity(root)
        except (OSError, ValueError, ImportError) as exc:
            raise TrustProtocolError("native repository source owner unavailable") from exc
        expected = item.source_authority.source_snapshot
        if subject != item.source_authority.subject_instance_id or (
            snapshot.sha256,
            snapshot.byte_count,
            snapshot.entry_count,
        ) != (expected.sha256, expected.byte_count, expected.entry_count):
            raise TrustProtocolError("native source differs from original repository admission")
    lock_admitted, lock_revision, groups, lock_roots = lock_owner._selected_projects(
        prework.journal,
        prework.run_id,
        prework=prework,
    )
    if lock_admitted != admitted or lock_revision != revision or lock_roots != roots:
        raise TrustProtocolError("native uv selection differs from original Fleet capture")
    locks = lock_owner._measure(groups, lock_roots, native_check=check_uv)
    tool_admitted, tool_revision, names = tool_owner._admitted_tools(
        prework.journal,
        prework.run_id,
        prework=prework,
    )
    if tool_admitted != admitted or tool_revision != revision:
        raise TrustProtocolError("native tool selection differs from original Fleet capture")
    tools = tool_owner._observe(names, authority)
    env = environment_owner._observe(
        prework.journal,
        prework.run_id,
        control_state_root=control_state_root,
        prework=prework,
    )
    backend = capability_owner._observe(
        prework.journal,
        prework.run_id,
        control_state_root=control_state_root,
        prework=prework,
    )
    if (
        env[:2] != (admitted, revision)
        or backend[:2] != (admitted, revision)
        or env[4] != backend[4]
    ):
        raise TrustProtocolError("executor and backend owner observations disagree")
    observed_environment = env[4]
    end, end_revision = prework.read(journal=prework.journal, run_id=prework.run_id)
    if end != admitted or end_revision != revision:
        raise TrustProtocolError("original Run changed during native contract compilation")
    return {
        "run_id": prework.run_id,
        "resolved_plan_identity": admitted.target.execution_digest,
        "repository_identity": run_owner.admitted_run_repository_capture_identity(admitted),
        "source_identity": source_identity,
        "lock_identity": digest(lock_owner.LOCK_DOMAIN, canonical_bytes({"locks": list(locks)})),
        "native_tool_identity": digest(
            tool_owner.TOOL_DOMAIN, canonical_bytes({"tools": list(tools)})
        ),
        "execution_environment_identity": observed_environment.execution_environment_identity,
        "backend_capability_manifest_identity": backend[5],
        "executor_generation": observed_environment.executor_generation,
    }


def compile_native_prework_run_contract(
    *,
    prework: PreworkRunCapture,
    deployment_policy: DeploymentRunContractPolicy,
    path_authority: StrictHostPathAuthority,
    control_state_root: Path | None = None,
) -> AdmittedRunContract:
    """Freeze one Run contract; no mutation and no fallback for missing observers.

    This is an opt-in pre-admission compiler; the fixed six issuers still must
    independently remeasure, sign and verify it inside the admission transaction.
    """
    if (
        type(prework) is not PreworkRunCapture
        or type(deployment_policy) is not DeploymentRunContractPolicy
        or not isinstance(path_authority, StrictHostPathAuthority)
        or (
            control_state_root is not None
            and (not isinstance(control_state_root, Path) or not control_state_root.is_absolute())
        )
    ):
        raise TrustProtocolError(
            "original prework, deployment policy and native path owners required"
        )
    deployment = deployment_policy.read()
    authority = tool_owner._trusted_path_authority(path_authority)
    first = _owner_observation(prework, authority, control_state_root, check_uv=True)
    second = _owner_observation(prework, authority, control_state_root, check_uv=False)
    if first != second:
        raise TrustProtocolError("native Run contract identities changed while compiling")
    admitted, _ = prework.read(journal=prework.journal, run_id=prework.run_id)
    active_steps = {
        step for item in admitted.repository_admissions for step in item.active_step_ids
    }
    for field in ("step_envelopes", "step_teardown_envelopes"):
        declared_steps = deployment[field]
        if (
            not isinstance(declared_steps, dict)
            or not active_steps
            or set(declared_steps) != active_steps
        ):
            raise TrustProtocolError(f"deployment {field} does not match exact active Plan Steps")
    _apply_native_agent_effect_policy(
        deployment,
        deployment_policy.agent_effect_policy,
        deployment_policy.agent_policy_source,
    )
    # Detect substitution after the first observations but before publication.
    if deployment_policy.agent_policy_source is not None:
        assert deployment_policy.agent_effect_policy is not None
        deployment_policy.agent_policy_source.require_matches(deployment_policy.agent_effect_policy)
    return AdmittedRunContract.freeze({**deployment, **first})
