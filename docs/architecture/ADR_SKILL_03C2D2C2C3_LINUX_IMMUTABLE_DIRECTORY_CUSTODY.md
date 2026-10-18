# SKILL-3C2D2C2C3 — Linux immutable staged-directory entry custody

Status: DEVELOPMENT IMPLEMENTATION, NOT RUN AUTHORIZATION OR NATIVE CERTIFICATION.

## Problem and ownership

The existing Agent source is verified, DAC sealed (0400/0500), and optionally
protected with Linux fs-verity per file. `fs-verity` protects file contents,
not directory entries: the same OS owner can otherwise remove and replace a
staged file after the last userspace verification. The original Fleet attempt
stager owns source materialization. The existing LACE `OriginalBindSourceLease`
owns native source FD pinning, not a separate Agent runtime/authorization plane.

## New explicit Linux option

The Fleet source stage accepts `require_kernel_directory_immutable=True`, but
only together with `require_kernel_file_verity=True`. It enables the Linux
`FS_IMMUTABLE_FL` directory flag via the actual `FS_IOC_GETFLAGS` and
`FS_IOC_SETFLAGS` ioctls on each already-open staged directory inode, bottom-up,
after member readback, permissions, and fsync. It preserves other inode flags
and independently reads the flag after setting it. The native long-size ioctl
ABI works on both 32-bit and 64-bit Linux processes.

The original LACE source FD lease accepts
`require_agent_kernel_directory_immutable=True`, also only together with
`require_agent_kernel_file_verity=True`. It checks the kernel immutable flag on
every directory during its no-follow source tree walk at FD pinning and again
at pre-argv and FD-argv consumption. Each file is independently measured using
existing fs-verity kernel digest checks. The signed mount roster and hard Agent
`RESOURCE_INTENT`/launch refusal are unchanged. Missing capability, filesystem
support, or immutable flags fail closed; neither policy nor Agent YAML may
silently turn the option on or weaken an already required mode.

## What this proves, and what it does not

These are genuine Linux filesystem operations but tests of positive ioctl
responses are mock-backed, not native kernel certification. Setting
`FS_IMMUTABLE_FL` normally requires CAP_LINUX_IMMUTABLE; unprivileged Windows
WSL and hosted filesystems may not support the requested behavior. On any
unsupported host the explicit mode rejects instead of degrading to chmod.

The flag protects directory inode entries from ordinary modification on a
capable filesystem. It does not prevent a privileged actor with permission to
clear flags, protect a replaceable ancestor (the original O_PATH source FD
pins the specific root inode), or prove filesystem mount/source exclusivity.
It also complicates cleanup: immutable entries must be unsealed by the
privileged custodian before their directories can be removed. No positive
Agent execution is enabled, so physical grant consumption, process namespace
and cgroup confinement, cancellation, revocation, teardown/unseal ownership,
native WSL qualification and release remain blocked. No second signer,
registry, executor, journal, or Hashmarks runtime agent is created.

## Acceptance / negative controls

- A directory ioctl that fails with EPERM/EOPNOTSUPP/ENOTTY/EINVAL rejects.
- The readback flag is required after enabling, not inferred from a successful
  `FS_IOC_SETFLAGS` syscall.
- The stage refuses directory custody without file fs-verity *before creating*
  any staging directory.
- Staging seals the entire hierarchy bottom-up and never publishes a usable
  receipt if any kernel operation fails.
- The LACE lease refuses an absent flag or a flag changed since FD pinning;
  default prework compatibility remains non-executable.
- Existing signed grants, source-roster/FD paths, architecture constraints and
  no-Agent-execution fences remain regression covered.
