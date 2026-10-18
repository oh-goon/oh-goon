# P1/B27B2A — original signed static native mount source authority

Status: **development-only**; not a native physical custody certificate.

## Why

P1B27B1 validated the complete Bubblewrap argv but signed `RESOURCE_INTENT.resources.mounts` still covered managed sources, host tools and optional read-only workspace, not the ordinary runtime-root, generated `/etc` and writable attempt mount sources. A post-reservation change to those source paths could therefore leave the signed `mounts` digest unchanged even when Bubblewrap's argv checker correctly described the altered request.

## One owner and execution order

1. `prepared_original_native_mounts(request)` derives immutable source/target/bind-mode records from the already prepared **original** Fleet Step attempt. No caller-provided plan, duplicate source parser or dynamic mount ID is trusted.
2. The same `OriginalMountIntentDescriptor.resource_identity` signs those records within existing `RESOURCE_INTENT.resources.mounts`. Its original managed-input and toolchain authority remains unchanged; no additional journal writer or signer is introduced.
3. The existing `LocalLaceJournalReservation.reserve` commits intent before cgroup/network/mount allocation. The existing Bubblewrap entrypoint checks the same committed `mounts_identity` immediately on return and again before mount argv construction.
4. The original Bubblewrap argv verifier uses exactly that same prepared native roster, plus previously admitted managed inputs, host tools, dynamic nested cgroup resource and sealed atomic FD bindings. Unknown native mount-producing switches fail closed.
5. The existing X0 checker and its normal WSL rings reject removed source classes, changed mode/source/target, missing signed serialization, missing consumption or duplicate projection.

The signed static roster binds `/`, `/etc/passwd`, `/etc/group`, `/etc/nsswitch.conf`, `/etc/hosts`, `/workspace` (correct read-only or writable mode), `/home/fleet`, `/tmp`, `/cache` unless supplied as a managed input, and `/config`.

## Explicit limits

These records pin **source-path authority**, not source inode identity, immutable file contents, or a physical kernel mount. Writable sources cannot be represented as immutable content hashes. A source path may still be replaced after reservation; runtime file objects must be held through the actual mount operation to prevent that race. The runtime's dynamically allocated cgroup mount, sealed atomic memfd kernel objects and their FD lifetimes are not yet signed as a complete physical resource envelope. The shared LACE observer has not independently proven actual mounted identities, namespace/FD holders or post-teardown absence. An admitted Agent Step remains denied.

Next: **P1B27B2B**, original signed dynamic source handle/FD-inode lifetime and consumption-time binding, then B27 original-observer mount+FD+namespace presence, bounded cleanup and independent absence. Do not create an Agent-specific `RESOURCE_INTENT`, physical observer, backend or recovery owner. Keep native WSL, Gate A/B/C, AG-03/04, APP PASS and release blocked until exact evidence exists.
