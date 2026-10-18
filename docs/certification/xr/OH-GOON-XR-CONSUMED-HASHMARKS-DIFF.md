# Consumed Hashmarks differential

- `hashmarks.verification_selection`: CONSUMED_RUNTIME_CONTRACT — byte-identical, no migration.
- `hashmarks.producer_identity`: CONSUMED_RUNTIME_CONTRACT — changed; pin canonical implementation identity independently of runtime self-report.
- `hashmarks.consumer_conformance`: PRODUCER_IDENTITY/CONFORMANCE SURFACE — changed producer-side implementation; no new Oh-Goon runtime dependency required.
- `hashmarks.qualified_identity`: unconsumed by this adapter; unchanged in compared wheels.
- `hashmarks.evidence_context`: NEW_OPTIONAL_CAPABILITY — canonical-only, not adopted.
- `hashmarks.contract_surface`: NEW_OPTIONAL_CAPABILITY — canonical-only, not adopted.
- CodeMap decomposition/additions: UNCONSUMED_HASHMARKS_INTERNAL for this rebase.

No canonical delta was found that requires Oh-Goon to reproduce Hashmarks repository semantics.
