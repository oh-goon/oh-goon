# AG-02B2B3B3B3 — signed source-stage mount identity (development)

**Architecture:** Extend OH-Goon’s original Run/Step execution authority; do not
create or embed an agent runtime. The source capture is read once. The original
Step-attempt stages it once and obtains an `OriginalSandboxMount` from the exact
private staged tree. The **existing** signed `AGENT_SOURCE_PREWORK_STAGED` record
now includes `source_mount: {source, target, sha256}`, with its existing stage
identity and `effect_permission=NONE`. No second signer/event/resolver/path.

The original Fleet `inspect_original_agent_source_mount_for_attempt` recomputes
the proposal using the original private attempt owner and requires exact equality
to that signed record, *inside the same session-owned authority transaction*.
Earlier valid stage receipts without this new field remain verifiable history,
but cannot be used for mount preflight or silently promoted to mount authority.

This is **still prework, never native effect permission**. The original
`RESOURCE_INTENT` rejects admitted Agent Steps and any stage prework. The
Bubblewrap pre-effect guard and signed mount-plan consumer are unchanged.
Actual read-only agent mount, FD-bound kernel object, independent observer custody,
post-cleanup physical absence, real signer/root Gate A and AG-03/04 egress
control are NOT IMPLEMENTED / NOT PROVEN by this checkpoint.

## Permanent invariants / regressions

- Original Fleet Stage inspection must occur before the original signed stage.
- The single original journal signer includes and validates all mount fields.
- The read-only Fleet proposal cannot match unless the signed fields match.
- Legacy signed stages lacking the mount cannot be upgraded silently.
- A changed source tree, target, path, stage, Step/Run or signer must fail.
- `AGENT_PREWORK` native ring and existing X0 architecture checker enforce it.
- QENV1 prepare once / verify once / verified-interpreter-only remains unchanged.

**Next:** original signed `RESOURCE_INTENT` adoption and read-only Bubblewrap
consumption with independent physical witnesses; no dual execution or policy path.
