# Hashmarks / Oh-Goon authority boundary

Hashmarks owns repository-intelligence and verification-selection semantics. Oh-Goon owns admission and physical execution semantics.

Hashmarks may identify, classify, associate, rank and recommend repository evidence. It may not schedule, launch, retry, bisect, resume or certify that evidence.

Oh-Goon may admit, freeze, regroup, execute, retry, bisect, resume and certify admitted evidence. It may not infer, reconstruct, repair or reinterpret Hashmarks repository intelligence.

Producer-defined identities and semantics are verified by consumers and are never independently recreated by consumers.

Schema evolution is not an authority-transfer mechanism. Unknown top-level Hashmarks fields are rejected by admission; execution-policy fields such as timeout, workers, retry, placement, execute-together and execution-order cannot silently acquire authority.

Hashmarks EvidenceUnit is not an Oh-Goon ExecutionShard. Hashmarks repository/evidence provenance and Oh-Goon execution/certification provenance remain separate authorities.
