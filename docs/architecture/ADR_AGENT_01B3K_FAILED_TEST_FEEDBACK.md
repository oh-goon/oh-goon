# ADR — AGENT-01B3K failure feedback through original Step outputs

**Decision:** Keep all failure and output semantics in existing Fleet publishers and the original sequencer. A nonzero **direct** `ResolvedCommandStep` with explicit `on_failure=continue`, declared bounded outputs, and `retry.max_attempts=1` may publish diagnostic files. It remains a FAILED Step. Existing output refs bind those exact validated UTF-8 bytes for later authored Steps with `step_status` conditions. The original Game Tape owns the record. Strict-mode failure publication is not enabled because its original seal/publication contract does not authorize it; do not weaken that contract to mirror direct behavior. No separate state machine, runner, agent edit permission, or reinterpretation of native test status.

**Exclusions:** No automatic model retry, no Agent-proposal publication on failure, no Step-output publication on retried failed attempts, and no success rollup override. When report files are missing or invalid, the contract fails without publishing their value.

**Follow-on:** Operator-selectable bounded adaptive feedback with source revision recapture and original Fleet-run attempt grouping. Keep ordinary local execution independent of optional high-security qualification.
