# P1/B27A — Signed host-tool mount targets in one original physical scope

Status: implemented development-only; physical absence and Agent execution remain BLOCKED.
Parent: exact T0B2B2A source. No new authority, observer, scheduler or mount builder.

## Gap closed

`OriginalMountIntentDescriptor.resource_identity` already signed the original
host-tool name, snapshot source and SHA-256. Bubblewrap mounted those tools at
`SANDBOX_PATHS.toolchain / name`, and mounted admitted `sh` also at `/bin/sh`.
However, `binding_targets`—used for original Run-owned LACE recovery sampling—
previously omitted both physical mount classes. Signed *sources* therefore
could be correct while the independent physical query omitted a holder.

## Single-owner correction

- The existing `OriginalMountIntentDescriptor` now projects exact original
  host-tool paths and the shell alias through `binding_targets`.
- Names reject path traversal / separators, non-canonical target counts and
  managed input mounts that shadow tools or the optional read-only workspace.
- The signed B12C mount identity (managed sources + tool source snapshots) is
  UNCHANGED. B22/B24 original-owner sampler still consumes the one descriptor.
- All target scope limits reject before native observation; the existing shared
  Linux observer, journal and outcome classifier are unchanged.
- X0 architecture/negative mutation tests reject dropped host-tool target
  projection, alias, consumer drift, missing scope checks or fake success.

## Explicitly not proven

This makes declared host tools *eligible for complete observation*; it does not
prove that an actual mount was observed or absent after cleanup. The signed
resource model still needs the runtime root, standard writable mounts, atomic
memfd/FD targets, and other effectful runtime mount classes. The staged Agent
source is still prohibited from effectful admission. B27 native FD/mount/
namespace custody, independent physical absence and actual Gate B remain open.

QENV1 remains prepare once, verify once, execute independent rings through one
verified interpreter; never synchronize/install/patch inside a ring.
