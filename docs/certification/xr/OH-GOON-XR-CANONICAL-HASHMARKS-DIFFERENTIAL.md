# Canonical Hashmarks v0.11.21 differential

Status: PASS for the consumed Oh-Goon boundary.

Compared retained development wheel `sha256:2e8b81e8...b504c` with canonical wheel `sha256:40e6c2af...51d21`.
Both distributions report version `0.11.21`, so version equality is explicitly non-authoritative.

Wheel member result: 114 old members, 139 canonical members, 11 changed common files, 26 added files, 1 removed file.
The consumed `hashmarks/verification_selection.py` module is byte-identical (`sha256:e0400538...47aa3`).
`hashmarks/producer_identity.py` and producer-side conformance surfaces changed; canonical also adds producer-owned evidence/context and refactored CodeMap modules.

Decision: preserve the existing verification-selection adapter semantics. Rebase exact producer artifact and trusted producer implementation identity only. New producer APIs remain unconsumed unless a future Oh-Goon invariant demonstrates need.
