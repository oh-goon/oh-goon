# Canonical Hashmarks v0.11.21 same-version rebase — implementation request

Parent: exact Oh-Goon 1267.0.209 FINAL CANDIDATE.
Scope: canonical producer artifact/identity rebase only; no Hashmarks feature development; no CL-07/08 optimization.

Gate/XR disposition:
- Native complete `make init` + `make test`: BLOCKED/UNCONFIRMED in ChatGPT runtime; required before Oh-Goon canonical promotion.
- OG-XR-01 same-version drift: PASS.
- OG-XR-01B supply substitution: PASS.
- OG-XR-02 consumed differential: PASS.
- OG-XR-02B producer conformance consumption: PASS, no new production helper required.
- OG-XR-03 semantic duplication: PASS, no new removal required.
- OG-XR-04 boundary races: PASS by retained deterministic regressions.
- OG-XR-04B evidence-context cross-pair: NOT APPLICABLE; evidence-context is not consumed.
- OG-XR-05 agent routing: NO SOURCE CHANGE REQUIRED; no fresh systematic ambiguity evidence.
- OG-XR-06 evidence economics: PASS/CLOSED in 1267.0.209 CL-01.
- OG-XR-07 cache ownership: PASS by existing cache boundary.
- OG-XR-08 historical series immutability: PASS.
- OG-XR-09 document authority: PASS.
- OG-XR-10 update surface: PASS.
- OG-XR-11 single selectable producer: PASS.

Implementation: replace retained wheel bytes, update exact artifact authority and dependency-resolution evidence, independently pin producer implementation authority, retain old series as history, create new series, and add fail-closed same-version/stale-runtime regressions.
