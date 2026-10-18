# ADR — WF-03C2C original admitted Run → Step process request

**Decision:** The existing `RUN_ADMITTED` journal is the sole consent source. The existing Fleet runner and prepared-resource owner transport consent/scope to every original Step command. Never generate or infer new consent from a Plan/Step. Preserve exact Run identity across prepare→execute, reject nonmatching Run before physical work, and leave `trusted_local_effect_authority=None` until supplied by original issuer.

**Why:** Earlier development introduced a true supervised local backend and durable Run consent, but the actual Step process request did not inherit it. This change closes that identity-transport gap without inventing another executor, signer or security gate.

**Security boundary:** consent is NOT authority for effects. Strict capability checks remain as before, and the TL-1 backend refuses work without effect-time Enforcer/LACE authorization. Host access is ambient, not a sandbox. Interactive issuance, configured inventory, prework audit and original effect-time authority remain independent open gates.

**Regression:** missing pair refused before creating workspace; TL-1 backend without consent refused; exact pair propagated into StepRuntime and consecutive command ordinals; no effect-authority object invented; wrong Run refused before attempt work.
