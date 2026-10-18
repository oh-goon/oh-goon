# Section 5 — Scenario and adversarial ownership reasoning

For each proposal, state whether it is valid. If invalid, identify the canonical owner and the forbidden mirror/shortcut.

## Q21
A developer puts repository eligibility and admission semantics directly into a FastAPI route because the values are only needed by that endpoint. Review the proposal.

## Q22
A Plan author adds `steps:` with a shell command directly to a Plan to avoid creating a small Atomic Goon. Review the proposal.

## Q23
Runtime code reparses a Goon YAML and rediscovers imports because it wants source positions closer to execution. Review the proposal.

## Q24
A frontend feature needs a backend response field that is missing from `@generated/*`, so it recreates the field and endpoint metadata in authored TypeScript. Review the proposal.

## Q25
A generated frontend contract has a typo, so a developer edits `generated/frontend/` directly and commits the fix. Review the proposal.

## Q26
A Schedule feature needs to run the same Plan on many repositories, so the developer adds `all_repositories` and repeated repository-discovery semantics to the Schedule definition. Review the proposal.

## Q27
Game Tape shows a failed step. A developer adds a Retry button inside Game Tape that directly starts recovery work. Review the proposal.

## Q28
A cancellation button calls `cancelFleet()` without a run ID and lets the provider choose whichever run is currently active. Review the proposal.

## Q29
A cancellation confirmation remains true when the displayed Run changes from A to B because the component will probably re-render immediately. Review the proposal.

## Q30
A newer Fleet snapshot no longer lists Run A as active. The frontend marks A `COMPLETED` solely from that absence. Review the proposal.

## Q31
A Schedule scheduler invokes all active Schedules immediately whenever the Oh-Goon process starts, then continues at their normal interval. Review the proposal.

## Q32
Tests inject a `MutationController` and `ReadController` backed by different causality clocks into one `ApiClientProvider`. Review the proposal.

## Q33
A developer cannot decide whether new backend behavior belongs in core, runtime, integrations, or services, so they create `backend/src/oh_goon/utils.py` as a neutral home. Review the proposal.

## Q34
A feature needs a component already private to another feature, so it imports that private component directly instead of going through app/domain contracts. Review the proposal.

## Q35
Native dependency materialization fails on a certification machine because registry/TLS access is unavailable. A developer proposes adding repository-owned dependency download/cache fallback Steps and broad `host_unrestricted` egress to later APP certification Steps. Review the proposal.

## Q168
Evidence-selection challenge: A Schedule scheduler invokes all active Schedules immediately whenever the Oh-Goon process starts, then continues at their normal interval. Review the proposal. Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q169
Negative-boundary challenge: Tests inject a `MutationController` and `ReadController` backed by different causality clocks into one `ApiClientProvider`. Review the proposal. Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q170
Change-impact challenge: A developer cannot decide whether new backend behavior belongs in core, runtime, integrations, or services, so they create `backend/src/oh_goon/utils.py` as a neutral home. Review the proposal. If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q171
Failure-diagnosis challenge: A feature needs a component already private to another feature, so it imports that private component directly instead of going through app/domain contracts. Review the proposal. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q172
Cross-boundary synthesis: Native dependency materialization fails on a certification machine because registry/TLS access is unavailable. A developer proposes adding repository-owned dependency download/cache fallback Steps and broad `host_unrestricted` egress to later APP certification Steps. Review the proposal. Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.
