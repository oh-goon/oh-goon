# B20 X0 — first-slice ownership and call-chain inventory

Status: **STATIC PARTIAL INVENTORY / NOT PRODUCTION QUALIFICATION**. The B20
change adds no new production LACE, journal, scheduler or Fleet authority.

| Entry point / role | Canonical source owner or call edge | What B20 actually enforces | Missing dynamic evidence |
| --- | --- | --- | --- |
| CLI / API / Fleet entry | `services/fleet_run_lifecycle.py` (`FleetRunLifecycle`), `services/fleet_run_control_plane.py` | Static production control modules cannot call the operator-scoped diagnostic | Same original Run admitted once across every ingress; attacks remain Gate A |
| Original local resource reservation | `runtime/enforcer_lace_fleet_resource_owner.py` (`OriginalFleetLocalLaceReservation`) | No production import/dispatch to diagnostic observer | Original signer/independent root epoch, Run-bound resource intent |
| Execution / backend | `services/fleet_run_execution.py`, `services/fleet_run_lifecycle.py`; Bubblewrap isolation behind reservation | No added executor/alternate dispatcher in B20 | One actual backend effect per original Step under crash/retry |
| Orphan cleanup / recovery | `integrations/fleet_plan_local_lace_orphan_inventory.py`, `integrations/fleet_plan_local_lace_native_reaper.py`, `runtime/enforcer_lace_fleet_resource_owner.py` (`OriginalFleetLocalLaceRecoveryOwner`) | No altered cleanup caller, no additional LACE fact writer | OS-proof-backed cleanup, strict recovery lease fencing |
| Development observer | `repository_tooling/local_lace_physical_preflight.py` via explicit `[project.scripts] oh-goon-local-lace-physical-preflight` | CLI owner bound; production module imports and literal dynamic dispatch rejected; diagnostic cannot import execution authority | B12E-C1 refactor into shared read-only Linux observation mechanism |
| Original journal / Enforcer terminal decision | Existing original execution/journal owners, unchanged in B20 | B20 adds zero journal writers and zero verdict classifiers | Exactly once signed physical facts / Enforcer classification under Gate C |
| Game Tape projection | Existing Game Tape readers, unchanged in B20 | No new projection reader/writer | Full read-only no-redecision call-chain regression under D1 |
| Remote OpenSandbox | Existing backend dispatcher, unchanged in B20 | No alternate provider created by this checkpoint | Authenticated separate remote provider physical Gate B3 |

**X0 first-slice limits.** The static AST guard prevents direct imports and
constant-string dynamic calls of the diagnostic from authority-bearing package
owners. It does not prove all dynamic reflection, data-dependent dispatch or
remote network effects. Gate A/B/C and D2 must produce dynamic adversarial
traces for every enabled ingress/backend, single original Run, single journal
classification and terminal seal. An unobserved edge is unresolved, not PASS.

**B20 native witness.** The existing `qualify-development-wsl` target first
calls `source-transport-clean`, then invokes one B11 supervisor for every ring.
B20 adds `PHYSICAL_OBSERVER_##` for each repetition using seven pinned modules
(B13/B15/B16/B17/B18/B19/real Linux `/proc`), with JUnit per-module coverage,
zero skipped tests, process identity and descendant drain receipts. It does not
turn these observer fixtures into physically authenticated production facts.

**No semantic redecision.** B20 test success, the operator-selected diagnostic
CLI, Game Tape and recovery may not publish signed quiescence or teardown facts;
they remain diagnostic/read-only. Production ownership must be consolidated in
B12E behind the original Run's authenticated physical-fact publisher.
