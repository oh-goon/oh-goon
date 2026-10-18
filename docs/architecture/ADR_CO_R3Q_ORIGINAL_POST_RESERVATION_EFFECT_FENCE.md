# ADR CO-R3Q — Recheck original signer authority after resource reservation

**Status:** Implemented development-only, not physical certification, not release.

## Problem

CO-R3P requires the current signed Fleet admission and durable signer trust epoch in the original SQLite write cut that publishes a LACE `RESOURCE_INTENT`. A valid reservation precedes subsequent filesystem cgroup allocation, network effects and sandbox process launch. Revocation, cancellation or loss of the original Fleet execution lease after the initial commit could leave those later effects using a historically valid but currently stale reservation.

## Decision

The existing `OriginalFleetLocalLaceReservation` is the **only** admitted local Fleet authority. Add `require_effect_authority(proof)` as a *read-only validation through the same original `ExecutionSession.mutate` transaction*. Within the cut require live `validate_authority_in` (including CO-R3P signer epoch), original session lease, no cancellation, and verification of the original causal signed `RESOURCE_INTENT` at the supplied exact Run, Step, ordinal, attempt digest, mount digest and signed event sequence. Check original live authority on both sides of the verification.

The original `BubblewrapProcessIsolation.execute` calls that one owner immediately before `resource_lease.open`, before native network allocation, and immediately before `run_command` after existing mount/FD custody checks. In strict local LACE mode the absence of the revalidation callback rejects before effects. No second admission, trust store, signer, journal, native reaper, executor, observer or cleanup state machine is introduced. The standalone journal adapter is retained for **development tests only** and checks its original signed event, never a live signer trust epoch; production strict uses the Fleet session owner.

## Evidence and limits

Focus regressions revoke after reservation (no cgroup `open`), cancel after cgroup open (network effect blocked, original cleanup attempted), revoke after resource allocation observation (payload never launched), and tamper original signed intent. Architecture mutations guard all three native effect-adjacent calls and original CORE enrollment. Hosted tests cannot prove kernel-atomic revocation: there is an unavoidable gap between the last SQLite validation and an external kernel syscall. Already-running processes are not killed by these checks, and cleanup must remain available after revocation. Independent kernel-enforced LACE custody, escaped/foreign writer enumeration, mount/FD namespace completeness and destructive `rmdir` are **NOT PROVEN**. Do not promote diagnostic local cgroup emptiness to cleanup permission.

## Native qualification

Run the original exact-source `make qualify-development-wsl QUALIFY_EVIDENCE_DIR=../OH_GOON_CO_R3Q_NATIVE_EVIDENCE` in WSL, and the opt-in cgroup2 witness with a fresh user-delegated disposable subtree. Separately require independently provisioned LACE observer and signer/root facts before Gate A/B/C, writer-bearing Git retirement, APP PASS and release.
