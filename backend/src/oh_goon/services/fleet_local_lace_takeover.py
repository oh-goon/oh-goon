"""Original Fleet local-LACE takeover composition, without alternate admission.

An authenticated Fleet recovery *session* is a mandatory caller-supplied owner.
The native observer is bound to trusted backend configuration and holds the
original allocator locks. This is an explicit recovery hook; it does not run
on application startup without independently provisioned Fleet authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceRecoveryOwner
from oh_goon.integrations.fleet_plan_local_lace_crash_reconciliation import observe_native_crash
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError


@dataclass(frozen=True, slots=True)
class OriginalLocalLaceTakeoverCoordinator:
    cgroup_root: Path
    cgroup_state_dir: Path
    cgroup_lock: Path
    network_state_dir: Path
    network_lock: Path
    ip: str
    nft: str

    def require_original_backend(self, backend: object) -> None:
        """Bind every native observer path to the *deployed* Bubblewrap owner.

        No HTTP/CLI/user supplied coordinator may override the original
        executor inventory, even with a syntactically valid Run ticket.
        """
        from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

        if type(backend) is not BubblewrapProcessIsolation or not backend.require_local_resource_intent:
            raise CausalJournalError("original mandatory-intent Bubblewrap backend required")
        expected = (backend.cgroup_root, backend.cgroup_state_dir, backend.cgroup_lock,
                    backend.egress_state_dir, backend.egress_lock,
                    backend.ip_executable, backend.nft_executable)
        observed = (self.cgroup_root, self.cgroup_state_dir, self.cgroup_lock,
                    self.network_state_dir, self.network_lock, self.ip, self.nft)
        if (any(value is None for value in expected) or observed != expected
                or any(type(binary) is not str or not Path(binary).is_absolute()
                       for binary in (backend.executable, backend.ip_executable, backend.nft_executable))):
            raise CausalJournalError("native recovery observer differs from admitted executor inventory")

    def resume(self, owner: OriginalFleetLocalLaceRecoveryOwner, *,
               intent_run_seq: int, original_backend: object) -> tuple[int, int]:
        """Verify predecessor and obtain fresh native evidence under the new epoch.

        No native cleanup is executed; all returned receipts remain unqualified.
        """
        self.require_original_backend(original_backend)
        if type(owner) is not OriginalFleetLocalLaceRecoveryOwner:
            raise CausalJournalError("original Fleet recovery session required")
        if (any(not isinstance(p, Path) or not p.is_absolute() for p in (
                self.cgroup_root, self.cgroup_state_dir, self.cgroup_lock,
                self.network_state_dir, self.network_lock))
                or any(type(x) is not str or not x for x in (self.ip, self.nft))):
            raise CausalJournalError("trusted native recovery configuration required")
        return owner.resume_reconciled_takeover(intent_run_seq=intent_run_seq,
            native_observer=lambda subject: observe_native_crash(subject,
                cgroup_root=self.cgroup_root, cgroup_state_dir=self.cgroup_state_dir,
                cgroup_lock=self.cgroup_lock, network_state_dir=self.network_state_dir,
                network_lock=self.network_lock, ip=self.ip, nft=self.nft))
