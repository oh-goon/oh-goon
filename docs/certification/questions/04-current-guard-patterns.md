# Section 4 — Current guard and risk patterns

These questions are version-bound to this source checkpoint. Describe the current code, not an older bug report.

## Q16
How does `ScheduleScheduler` prevent a concurrent start/stop thread-handle race across the join boundary?

## Q17
Where does local process-group termination poll for exit, and what bounded escalation follows if the process group remains alive?

## Q18
Where is Kubernetes rollout verification implemented as a bounded polling loop, and what controls its sleep interval and timeout?

## Q19
Identify the stale-write/revision protection used for Shared Plans, League Lineups, and authored Fleet Plan source writes. What invariant is the revision enforcing?

## Q20
What prevents `ApiClientProvider` transport/client/controller authority from silently changing after mount, and what causality invariant is enforced when both controllers are supplied?

## Q163
Evidence-selection challenge: How does `ScheduleScheduler` prevent a concurrent start/stop thread-handle race across the join boundary? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q164
Negative-boundary challenge: Where does local process-group termination poll for exit, and what bounded escalation follows if the process group remains alive? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q165
Change-impact challenge: Where is Kubernetes rollout verification implemented as a bounded polling loop, and what controls its sleep interval and timeout? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q166
Failure-diagnosis challenge: Identify the stale-write/revision protection used for Shared Plans, League Lineups, and authored Fleet Plan source writes. What invariant is the revision enforcing? Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q167
Cross-boundary synthesis: What prevents `ApiClientProvider` transport/client/controller authority from silently changing after mount, and what causality invariant is enforced when both controllers are supplied? Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q215
An analysis tool plans to call the runtime invariant auditor once and use that result, plus the recovery models, as the complete catalog of every invariant finding code the product can emit. Is that valid? Distinguish a runtime safety-cut observation from the static finding types and concrete code-emission sites.

## Q219
A coverage review wants to introduce a new standalone failure-mode matrix document and manually map tests into it. Before adding another coverage authority, what existing Oh-Goon lifecycle-coverage owner must be inspected, and how should a genuinely missing failure mode be added without creating a parallel source of truth?
