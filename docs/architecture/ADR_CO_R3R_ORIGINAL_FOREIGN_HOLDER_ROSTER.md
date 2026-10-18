# ADR CO-R3R — Original foreign-mount holder roster must close around the scan

## Context

The shared Linux read-only observer already fences original PID/birth and namespace identities, bounds the foreign mount inventory, and validates each observed process. Unlike its namespace-FD and original mount-FD cousins, `_visible_foreign_mount_targets` did not re-enumerate the visible `/proc` PID roster at the end. A new foreign holder could appear after the first census and the probe could emit a stable-looking `NOT_OBSERVED` scoped fact.

## Decision

Continue using **only** `linux_physical_observer._visible_foreign_mount_targets`. After inspecting foreign mount records, rescan the same bounded visible PID names, compare with the initial census, and return `UNKNOWN` on drift/inaccessibility. Preserve original tracked PID and namespace revalidation and fail-closed exceptions. Do not create an alternative scanner, policy engine, reservation owner, cleanup owner, journal writer, Enforcer signer or LACE fact authority.

Enroll arrival/departure regressions, an original X0 negative-mutation guard and a host native detached-session namespace-FD negative witness in the same QENV1 CORE. An incomplete host `/proc` census is an explicit skip; it cannot be relabeled as success.

## Consequences and limitations

The additional check closes only a *sample-level observation race*. A process can escape a namespace after the final census, a PID namespace may be hidden, and detached mounts or foreign open FDs may survive. No finite local scan proves complete physical absence or grants teardown authority. Trust revocation checked before a syscall remains non-atomic with that syscall. Linux `rmdir(name, dir_fd=...)` cannot require the original target inode. Independent parent custody and physically complete Run/Step writer absence remain certification blockers. All established release gates stay blocked.
