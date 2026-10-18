# ADR — Effectful WSL fixture must use the original sparse-root mount scaffold

**Checkpoint:** P1B27_GATE_P1P2B2B1. **Parent:** P1P2B2B. **Status:** accepted development-only test-fixture repair, NOT release.

## Observed defect and decision

The exact P1P2B2A WSL native witness reached the real `/usr/bin/bwrap` process but failed constructing `/workspace` with `EROFS`, because its development fixture substituted the host `/` as the read-only runtime root. WSL host `/` does not guarantee OH-Goon's synthetic target directories. No FD-bound mount or kernel provenance was actually exercised; the native qualification was correctly rejected.

Use OH-Goon's existing `_create_mount_scaffold` for a private, disposable development root. Place only the shell, stat, awk, cat and their Linux dynamic dependencies in it as test-owned copies. This intentionally differs from admitted production runtime snapshots, so the witness continues to stub *only* the runtime snapshot verifier; the original `_command`, source FD pins, sealed memfd, combined descriptor roster and supervised `run_command` remain real and unchanged.

No host filesystem change, second mount builder, production authorization, physical signer/publisher or Agent permissions are allowed. Explicit scaffold coverage is in the always-selected physical-observer test ring; the effectful witness remains opt-in and **must fail** when Bubblewrap or kernel mount support is unavailable.

## Authority and evidence boundaries

The original Run/Step Enforcer and LACE resource intent remain the only admission and physical authority path. This fixture is a mechanical probe. The WSL evidence archive and its source hashes are historical failed development evidence, not an acceptable Gate B physical-fact receipt. Do not reinterpret local chroot checks, process quiescence, a sealed fixture, a hash chain, or `/proc` visibility as independently authenticated physical facts or teardown proof.

A subsequent exact-source WSL run must verify the corrected root construction and actual `--bind-fd` mounted kernel object identities. Real independent observer custody, Run attribution, P3 physical absence and all release gates remain separate.
