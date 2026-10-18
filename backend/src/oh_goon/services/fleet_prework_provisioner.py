"""M171: deployment-owned, fail-closed selection of a pre-enrolled Fleet Run authority.

This is not a contract compiler or an issuer. It resolves an explicit Run by ID,
reobserves the *existing* inventory and independent public trust-root owners,
and returns one frozen M170 admission binding. No Plan/API supplied signing keys,
ambient provisioning fallback, or anonymous issuer callback is permitted.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.native_issuer.enforcer_lace_execution_environment_issuer import _inventory_snapshot
from oh_goon.native_issuer.enforcer_lace_signer_custody import deployment_signer_policy_snapshot
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.enforcer_lace_issuer_keyring import (
    IssuerKeyReference, load_explicit_issuer_verifier,
)
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_policy_enrollment import FleetPolicyEnrollment
from oh_goon.services.fleet_strict_policy_enrollment import StrictFleetPolicyEnrollment


_NATIVE_CREDENTIAL_FIELDS = ("Uid", "Gid", "NoNewPrivs", "CapInh", "CapPrm", "CapEff", "CapAmb")
_NATIVE_THREAD_FIELDS = (*_NATIVE_CREDENTIAL_FIELDS, "Pid", "Tgid", "Groups")
_NATIVE_DECIMAL = re.compile(r"[0-9]+\Z")
_NATIVE_HEX = re.compile(r"[0-9a-fA-F]{1,16}\Z")


def _native_task_starttime(tid: int) -> int:
    """Kernel birth identity; a task-ID census alone misses exit/reuse (ABA).

    The comm field in /proc/.../stat is parenthesized and can contain spaces
    and parentheses, so fixed whitespace indexes over the full line are not
    valid. Field 22 (starttime) is index 19 after the final `) ` delimiter.
    """
    try:
        raw = Path(f"/proc/self/task/{tid}/stat").read_text(encoding="ascii")
        beginning = raw.find(" (")
        ending = raw.rfind(") ")
        if beginning < 1 or ending <= beginning + 1:
            raise TrustProtocolError("invalid native signer task birth identity")
        head = raw[:beginning]
        fields = raw[ending + 2:].split()
        if (_NATIVE_DECIMAL.fullmatch(head) is None or int(head) != tid
                or len(fields) < 20
                or _NATIVE_DECIMAL.fullmatch(fields[19]) is None):
            raise TrustProtocolError("invalid native signer task birth identity")
        return int(fields[19])
    except TrustProtocolError:
        raise
    except (OSError, UnicodeError, ValueError, OverflowError) as exc:
        raise TrustProtocolError("native signer task birth identity unavailable") from exc


def _native_status_fields(contents: str, required: tuple[str, ...]) -> dict[str, str]:
    """One strict parser for the caller and each native Linux task status."""
    found: dict[str, str] = {}
    for line in contents.splitlines():
        field, separator, value = line.partition(":")
        if separator and field in required:
            if field in found:
                raise TrustProtocolError("ambiguous native signer credentials")
            found[field] = value.strip()
    if set(found) != set(required):
        raise TrustProtocolError("incomplete native signer capability evidence")
    return found


def _native_status_privileges(fields: Mapping[str, str]) -> tuple[tuple[int, ...], tuple[int, ...], int, tuple[int, ...]]:
    uids = fields["Uid"].split()
    gids = fields["Gid"].split()
    cap_names = ("CapInh", "CapPrm", "CapEff", "CapAmb")
    if (len(uids) != 4 or len(gids) != 4
            or any(_NATIVE_DECIMAL.fullmatch(value) is None for value in (*uids, *gids))
            or _NATIVE_DECIMAL.fullmatch(fields["NoNewPrivs"]) is None
            or any(_NATIVE_HEX.fullmatch(fields[name]) is None for name in cap_names)):
        raise TrustProtocolError("invalid native signer credentials")
    return (tuple(map(int, uids)), tuple(map(int, gids)),
            int(fields["NoNewPrivs"]), tuple(int(fields[name], 16) for name in cap_names))


def _native_signer_thread_census(
    uids: tuple[int, ...], gids: tuple[int, ...], groups: tuple[int, ...],
) -> tuple[int, ...]:
    """Fail closed if ANY Linux thread retains separate filesystem/exec privilege.

    Linux credentials and NoNewPrivs can differ across threads. Inspecting the
    caller alone is not proof of process-wide separation. These observations
    remain non-attested; an external deployment witness is still required.
    """
    try:
        pid = os.getpid()

        def task_ids() -> tuple[int, ...]:
            entries = os.listdir("/proc/self/task")
            if (not entries or any(_NATIVE_DECIMAL.fullmatch(entry) is None
                                   for entry in entries)):
                raise TrustProtocolError("native signer task inventory unavailable")
            tids = tuple(sorted(map(int, entries)))
            if len(set(tids)) != len(tids):
                raise TrustProtocolError("ambiguous native signer task identity")
            return tids

        before = task_ids()
        if threading.get_native_id() not in before:
            raise TrustProtocolError("caller absent from native signer task inventory")
        births: list[int] = []
        for tid in before:
            birth_before = _native_task_starttime(tid)
            fields = _native_status_fields(
                Path(f"/proc/self/task/{tid}/status").read_text(encoding="ascii"),
                _NATIVE_THREAD_FIELDS,
            )
            observed_uids, observed_gids, nnp, caps = _native_status_privileges(fields)
            observed_groups = fields["Groups"].split()
            if (len(observed_groups) != len(groups)
                    or any(_NATIVE_DECIMAL.fullmatch(g) is None for g in observed_groups)
                    or tuple(sorted(map(int, observed_groups))) != groups
                    or _NATIVE_DECIMAL.fullmatch(fields["Pid"]) is None
                    or _NATIVE_DECIMAL.fullmatch(fields["Tgid"]) is None
                    or int(fields["Pid"]) != tid or int(fields["Tgid"]) != pid
                    or observed_uids != uids or observed_gids != gids
                    or nnp != 1 or any(caps)):
                raise TrustProtocolError("signer thread retains independent OS authority")
            if _native_task_starttime(tid) != birth_before:
                raise TrustProtocolError("native signer thread replaced during custody observation")
            births.append(birth_before)
        if task_ids() != before:
            raise TrustProtocolError("native signer task inventory changed during custody observation")
        # A stable task-ID set is not enough: the same numeric TID can be
        # recycled after an exit. Reobserve all birth identities at the cut.
        if any(_native_task_starttime(tid) != birth for tid, birth in zip(before, births)):
            raise TrustProtocolError("native signer thread replaced during custody observation")
        return (pid, len(before), *before, *births)
    except TrustProtocolError:
        raise
    except (OSError, UnicodeError, ValueError, OverflowError) as exc:
        raise TrustProtocolError("native signer task census unavailable") from exc


def _native_unprivileged_signer_identity() -> tuple[int, ...]:
    """Observe the original Linux process's ability to regain write authority.

    A distinct euid is not OS custody isolation if a saved root UID, a
    permitted/inheritable capability, or a later privileged exec can bypass
    the public-root DAC fence. Only strict foreign-UID deployments need this
    native Linux precondition. This is an observation, not host attestation.
    """
    if not (hasattr(os, "getresuid") and hasattr(os, "getresgid")):
        raise TrustProtocolError("native signer credential observation unavailable")
    try:
        uids = os.getresuid()
        gids = os.getresgid()
        groups = tuple(sorted(os.getgroups()))
        # This process may be multi-threaded. /proc/self/status describes the
        # group leader; only /proc/thread-self/status describes this caller's
        # credentials. The process-wide census below must also cover all tasks.
        status_text = Path("/proc/thread-self/status").read_text(encoding="ascii")
        kernel_uids, kernel_gids, nnp, capabilities = _native_status_privileges(
            _native_status_fields(status_text, _NATIVE_CREDENTIAL_FIELDS))
    except TrustProtocolError:
        raise
    except (OSError, UnicodeError, ValueError, OverflowError) as exc:
        raise TrustProtocolError("native signer credential observation unavailable") from exc
    if (len(uids) != 3 or len(gids) != 3 or len(set(uids)) != 1
            or len(set(gids)) != 1 or uids[0] == 0
            or kernel_uids != (*uids, uids[1])
            or kernel_gids != (*gids, gids[1])
            or os.geteuid() != uids[1] or nnp != 1 or any(capabilities)):
        raise TrustProtocolError("signer retains privileged OS authority")
    threads = _native_signer_thread_census(kernel_uids, kernel_gids, groups)
    return (*kernel_uids, *kernel_gids, len(groups), *groups, *threads, nnp, *capabilities)


@dataclass(frozen=True, slots=True)
class FleetPreworkProvisioner:
    """Fixed, explicit Run enrollment; no lazy/default credentials or contract fabrication."""

    enrolled: tuple[FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment, ...]
    executor_inventory_file: Path
    trusted_key_directory: Path
    issuer_references: Mapping[str, IssuerKeyReference]
    control_state_root: Path | None = None
    public_root_owner_uid: int | None = None

    def __post_init__(self) -> None:
        if (type(self.enrolled) is not tuple or not self.enrolled
                or any(type(item) not in (FleetPreworkComposition, FleetPolicyEnrollment,
                                          StrictFleetPolicyEnrollment) for item in self.enrolled)
                or not all(isinstance(p, Path) and p.is_absolute() for p in (
                    self.executor_inventory_file, self.trusted_key_directory))
                or not isinstance(self.issuer_references, Mapping)
                or set(self.issuer_references) != AUTHORITY_FIELDS
                or (self.public_root_owner_uid is not None and (
                    type(self.public_root_owner_uid) is not int
                    or self.public_root_owner_uid < 0
                    or self.public_root_owner_uid == os.geteuid()))
                or (self.control_state_root is not None and (
                    not isinstance(self.control_state_root, Path)
                    or not self.control_state_root.is_absolute()))):
            raise TrustProtocolError("complete explicit Fleet deployment provisioning required")
        refs = dict(self.issuer_references)
        if any(type(r) is not IssuerKeyReference for r in refs.values()):
            raise TrustProtocolError("fixed issuer public-key references required")
        run_ids: set[str] = set()
        for binding in self.enrolled:
            if type(binding) is FleetPreworkComposition:
                binding.contract.verify()
                run_id = binding.contract.fields["run_id"]
            else:
                run_id = binding.run_id
            if run_id in run_ids:
                raise TrustProtocolError("duplicate enrolled Fleet Run authority")
            run_ids.add(run_id)
        object.__setattr__(self, "issuer_references", MappingProxyType(refs))

    def inspect_deployment_readiness(self, run_id: str) -> Mapping[str, str | int]:
        """Read-only, non-authorizing deployment diagnostic for one enrolled Run.

        Reuses the original native inventory, trusted-public-root and signer
        custody observations.  The returned *public* projection cannot be
        transported as a Fleet authority or used instead of final admission.
        Call this only from the deployment that already owns ``self``; no
        ambient CLI, Plan, JSON or environment-based provisioner discovery.
        """
        binding = self.resolve(run_id)
        configured = binding.configured
        # No paths, public key bytes, private keys, authenticators, signer
        # policy, or mutable enrollment objects escape this boundary.
        return MappingProxyType({
            "schema": "oh-goon.fleet-deployment-readiness/v1",
            "status": "OBSERVED_NOT_ADMITTED",
            "run_id": run_id,
            "enrollment_kind": type(binding).__name__,
            "executor_id": configured.executor_id,
            "executor_generation": configured.executor_generation,
            "execution_environment_identity": configured.execution_environment_identity,
            "backend_capability_manifest_identity": configured.capability_manifest_identity,
            "independently_verified_issuer_roots": len(self.issuer_references),
            "run_admission": "NOT_PERFORMED",
            "app_certification": "NOT_ESTABLISHED",
            "authenticated_replay": "NOT_AUTHORIZED",
            "physical_containment": "NOT_ESTABLISHED",
        })

    def inspect_independent_root_custody(self, run_id: str) -> Mapping[str, str | int]:
        """Strict diagnostic from the *existing* deployment provisioner.

        This is not native host attestation, a trust-epoch proof, an enrollment
        grant, or a replacement admission path. It demands that the actual
        configured public roots belong to a pinned *other* OS principal;
        the signer remains owned by the current process principal. The normal
        readiness observation checks all six keys, inventory and enrollment.
        Never serialize the provisioner or construct a second one for a probe.
        """
        owner_uid = self.public_root_owner_uid
        if owner_uid is None:
            raise TrustProtocolError("independent public-root owner not provisioned")
        original_principal = _native_unprivileged_signer_identity()
        euid = os.geteuid()
        original = self.inspect_deployment_readiness(run_id)
        # Preserve the read-only semantics of the original owner. The keyring
        # checked directory and leaf owners at their canonical read cut.
        # Re-resolve after observing the principal fence, not just before it.
        self.resolve(run_id)
        if (os.geteuid() != euid
                or _native_unprivileged_signer_identity() != original_principal):
            raise TrustProtocolError("Fleet deployment principal changed during custody observation")
        return MappingProxyType({
            "schema": "oh-goon.fleet-deployment-os-custody/v1",
            "status": "OBSERVED_NOT_ATTESTED",
            "run_id": original["run_id"],
            "executor_id": original["executor_id"],
            "executor_generation": original["executor_generation"],
            "independent_public_root_owner_uid": owner_uid,
            "signer_process_euid": euid,
            "issuer_roots_checked": original["independently_verified_issuer_roots"],
            "enrollment": "OBSERVED_NOT_ADMITTED",
            "external_provisioning_attestation": "NOT_ESTABLISHED",
            "app_pass": "NOT_ESTABLISHED",
            "physical_lace": "NOT_ESTABLISHED",
        })

    def require_enrolled_identity(self, run_id: str) -> None:
        """Cheap CLI ingress fence: prove enrollment membership without observing issuers.

        This is NOT authorization. The original resolve() and final atomic
        admission must still reobserve policy, signer, inventory and placement.
        """
        self._select_enrolled(run_id)

    def _select_enrolled(self, run_id: str) -> FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment:
        if type(run_id) is not str or not run_id:
            raise TrustProtocolError("explicit Fleet Run identity required")
        selected = tuple(item for item in self.enrolled
                         if (item.contract.fields["run_id"] if type(item) is FleetPreworkComposition
                             else item.run_id) == run_id)
        if len(selected) != 1:
            raise TrustProtocolError("Fleet Run is not explicitly provisioned")
        return selected[0]

    def resolve(self, run_id: str) -> FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment:
        """Resolve one exact Run, twice checking native roots and inventory for drift."""
        binding = self._select_enrolled(run_id)
        before = self._observe(binding)
        after = self._observe(binding)
        if before != after:
            raise TrustProtocolError("Fleet deployment authority changed during provisioning")
        return binding

    def _observe(self, binding: FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment) -> tuple[object, ...]:
        from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
        custody_before = self._require_separate_custody(binding)
        configured_path = os.environ.get(EXECUTORS_FILE_ENV)
        if configured_path != str(self.executor_inventory_file):
            raise TrustProtocolError("deployment executor inventory differs from authoritative selection")
        before = _inventory_snapshot()
        inventory = configured_executor_pool(control_state_root=self.control_state_root)
        if inventory is None:
            raise TrustProtocolError("deployment executor inventory is missing")
        if (binding.path_authority != inventory.path_authority
                or binding.control_state_root != self.control_state_root):
            raise TrustProtocolError("Fleet path or control-state authority differs from deployment")
        matching = tuple(item for item in inventory.executors
                         if item.identity.executor_id == binding.configured.executor_id)
        if len(matching) != 1:
            raise TrustProtocolError("Fleet executor is absent or ambiguous")
        observed = matching[0].resolve_environment(inventory.path_authority)
        if observed != binding.configured or observed != binding.current:
            raise TrustProtocolError("Fleet executor generation, configuration or backend changed")
        if type(binding) is FleetPreworkComposition:
            fields = binding.contract.fields
            if (fields["executor_generation"] != observed.executor_generation
                    or fields["execution_environment_identity"] != observed.execution_environment_identity
                    or fields["backend_capability_manifest_identity"] != observed.capability_manifest_identity):
                raise TrustProtocolError("Fleet Run contract differs from native executor authority")
        else:
            binding.deployment_policy.read()
            binding.configured.require_unchanged(binding.current)
        # The independent verifier owns the public file validation. The signer
        # policy observer additionally proves custody state without key exposure.
        verifier = load_explicit_issuer_verifier(
            self.trusted_key_directory, self.issuer_references,
            public_root_owner_uid=self.public_root_owner_uid,
        )
        roots = verifier.public_roots()
        if dict(binding.trusted_public_roots) != roots:
            raise TrustProtocolError("Fleet issuer roots differ from independently trusted files")
        snapshot = deployment_signer_policy_snapshot(
            key_directory=binding.key_directory, trusted_public_roots=roots)
        policy = json.loads(snapshot[2])
        if any(item["state"] != "active" for item in policy["issuers"].values()):
            raise TrustProtocolError("Fleet Run signing authority is revoked")
        after = _inventory_snapshot()
        custody_after = self._require_separate_custody(binding)
        if before != after or custody_before != custody_after:
            raise TrustProtocolError("Fleet executor inventory changed during provisioning")
        return (before, snapshot, observed, tuple(sorted(roots.items())), custody_before)

    def _require_separate_custody(
        self, binding: FleetPreworkComposition | FleetPolicyEnrollment | StrictFleetPolicyEnrollment,
    ) -> tuple[tuple[int, ...], ...]:
        """Public verifier roots may not alias or live inside the signer tree.

        This is a structural custody check, not a claim of separate host UIDs or
        a durable signer epoch. Each existing native owner still has to validate
        its directory, contents and generation at the original admission cut.
        """
        try:
            public = self.trusted_key_directory.resolve(strict=True)
            private = binding.key_directory.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise TrustProtocolError("independent Fleet trust and signer directories required") from exc
        if public == private or public.is_relative_to(private) or private.is_relative_to(public):
            raise TrustProtocolError("public trust roots must be separate from signer custody")
        if self.public_root_owner_uid is not None:
            # Distinct leaf ownership is insufficient if the signer principal
            # can replace any parent directory name. Saved-UID/capability
            # privilege and exec-time privilege gain also bypass DAC ownership.
            principal_before = _native_unprivileged_signer_identity()
            euid = os.geteuid()
            if euid == 0:
                raise TrustProtocolError("privileged signer cannot prove independent OS custody")
            identities: list[tuple[int, ...]] = []
            for ancestor in (self.trusted_key_directory, *self.trusted_key_directory.parents):
                try:
                    info = os.stat(ancestor, follow_symlinks=False)
                except OSError as exc:
                    raise TrustProtocolError("independent trust ancestry unavailable") from exc
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid == euid
                        or info.st_mode & 0o022):
                    raise TrustProtocolError("independent trust ancestry is signer-mutable")
                identities.append((info.st_dev, info.st_ino, info.st_uid, info.st_mode,
                                   info.st_mtime_ns, info.st_ctime_ns))
            principal_after = _native_unprivileged_signer_identity()
            if principal_before != principal_after:
                raise TrustProtocolError("Fleet deployment signer principal changed during prework")
            return (principal_before, *identities)
        return ()
