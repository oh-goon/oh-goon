# ADR CO-R3S — Identity-bound original cgroup physical observation

**Status:** accepted as an observational hardening only; physical custody, Gate B, and release remain BLOCKED.

## Problem

The shared read-only Linux physical observer checked `stat(cgroup_path)` for the original inode, then reopened `cgroup.events` and `cgroup.procs` by absolute pathname. A foreign actor could rename or replace the original cgroup between the identity check and the reads. The observer could label the replacement as a stable observation of the original. Such a diagnostic claim must never become physical absence or destructive permission.

## Decision

Maintain the original `oh_goon.integrations.linux_physical_observer._cgroup` owner and original physical observation port. Open one `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` directory descriptor, verify original `st_dev/st_ino`, and read **both** controls through `openat`-style `dir_fd` operations with `O_NOFOLLOW`, bounded ASCII and `fstat` checks. Revalidate original path identity and the pinned descriptor after the controls are read. Missing/replaced/control-symlink/oversized/malformed observation returns `UNKNOWN`, never a reliable original-inode fact. Retain `NOT_OBSERVED` only for original path already missing at entry, with the existing explicit global-absence disclaimer.

This is a **read-only diagnostic**. The cgroup owner remains solely responsible for any native effects; Enforcer/LACE remains the only authority for Run/Step actions. No second signer, policy, admission, cleanup, kernel observer, or decision-making component.

## Limits

An attacker can replace the cgroup basename at an arbitrary syscall boundary; `rmdir` does not support atomic inode-conditional removal. A pinned directory descriptor is not an externally provisioned kernel exclusion lock, cannot prove complete original writer absence, and cannot observe references in hidden PID namespaces. Local process/FD/mount scans, even if internally stable, are not independent custody. No native WSL kernel qualification or independently attested Enforcer/LACE generation custody has been performed for CO-R3S. **CO-R2b writer-bearing cleanup and Gate A/B/C, P3C, APP PASS, release remain blocked.**

## Verification

Original X0 structural guard rejects removal of handle-bound control reads, missing identity checks, and missing CORE enrollment. Adversarial tests exercise replacement before directory open, replacement between control reads (with foreign replacement and missing path), control symlink, oversized/non-ASCII controls, and stable original observations. Exact-source manifest and native QENV1 qualification selector are authoritative for this checkpoint. Focused tests cannot substitute for real separate-observer kernel qualification.
