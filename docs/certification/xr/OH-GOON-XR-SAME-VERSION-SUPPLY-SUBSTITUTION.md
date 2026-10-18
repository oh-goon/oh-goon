# Same-version dependency-supply substitution

Status: PASS in bounded source qualification.

Trusted canonical artifact: `sha256:40e6c2af7913ddbd82ceecb70d5ee9d8f1b861d355b95fdda84c7f4b06751d21`.
Trusted canonical producer implementation: `sha256:a5a94ed47b19541bfbd1f613a7cbaf455e943b22113b5d0ea6c8347619051ade`.
Historical same-version artifact/producer identities are retained only as negative/historical evidence.

New admission rule requires both exact artifact authority and exact imported producer implementation identity. A stale 0.11.21 runtime can no longer self-authorize merely because package version and its own generated evidence agree.

The vendor directory is structurally tested to contain exactly one selectable production Hashmarks 0.11.21 wheel, whose bytes must match the trusted artifact identity.
