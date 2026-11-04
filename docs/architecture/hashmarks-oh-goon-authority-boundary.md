# Hashmarks / Oh-Goon authority boundary

Hashmarks owns repository-intelligence and verification-selection semantics. Oh-Goon owns admission and physical execution semantics.

Production consumers obtain repository facts through the public
`oh_goon.integrations.hashmarks` API, whose explicit exports are the executable
capability census. The integration directory alone is not the boundary: direct
producer imports and private integration-submodule imports outside that package
are rejected. The [mandatory SSOT requirements](../development/OH_GOON_HASHMARKS_MANDATORY_SSOT_API_REQUIREMENTS.md)
own this contract; the [current handoff and migration limits](../development/HASHMARKS_EXECUTION_LAYER.md)
distinguish import protection from completed semantic adoption.

Hashmarks is required at runtime startup and for model-backed Agent repository evidence. The Agent receives producer task evidence from its exact captured source before reasoning. Missing evidence fails the Step before model disclosure. Producer evidence is presented with its provenance and coverage limits; the complete admitted review scope remains unchanged. See the [execution handoff](../development/HASHMARKS_EXECUTION_LAYER.md).

Hashmarks may identify, classify, associate, rank and recommend repository evidence. It may not schedule, launch, retry, bisect, resume or certify that evidence.

Oh-Goon may admit, freeze, regroup, execute, retry, bisect, resume and certify admitted evidence. It may not infer, reconstruct, repair or reinterpret Hashmarks repository intelligence.

Producer-defined identities and semantics are verified by consumers and are never independently recreated by consumers.

Hashmarks repository/content identity records observed source scope and
generation. Oh-Goon's execution-custody/materialization identity records the exact
bytes frozen, transported and exposed under admitted authority. Those distinct
facts may concern the same bytes. A matching path or digest does not transfer
custody or effect permission.

Repository interpretation of runtime paths, tracebacks, lines and symbols also
remains Hashmarks-owned through that public API. Oh-Goon captures and binds the
execution observation; Game Tape and diagnostics project it without deriving a
second repository meaning, changing frozen membership or owning Run state.
Unavailable producer precision remains unknown/refused, never a local semantic
fallback.

Schema evolution is not an authority-transfer mechanism. Unknown top-level Hashmarks fields are rejected by admission; execution-policy fields such as timeout, workers, retry, placement, execute-together and execution-order cannot silently acquire authority.

Hashmarks EvidenceUnit is not an Oh-Goon ExecutionShard. Hashmarks repository/evidence provenance and Oh-Goon execution/certification provenance remain separate authorities.
