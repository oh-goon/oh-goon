# Section 7 — Strict isolation and tool authority

## Q41
What authority is carried explicitly by each resolved strict Step, and where does repository filesystem authority come from? Name host authorities that must not be added as shortcuts.

## Q42
How is a strict host tool admitted and executed? Explain why ambient `PATH`, version fallback, or runtime substitution is forbidden after admission.

## Q43
What does `PlanStepCapabilities.tools` mean relative to a Step's primary executable, and what runtime authority must still remain unavailable?

## Q44
How are managed runtimes represented inside strict execution, and why must their original host filesystem topology never become sandbox semantics?

## Q45
Distinguish the owners of authoring intent, executable behavior, resolved identity fingerprinting, authorization, execution, and execution evidence around `goon.lock`.

## Q178
Evidence-selection challenge: What authority is carried explicitly by each resolved strict Step, and where does repository filesystem authority come from? Name host authorities that must not be added as shortcuts. Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q179
Negative-boundary challenge: How is a strict host tool admitted and executed? Explain why ambient `PATH`, version fallback, or runtime substitution is forbidden after admission. Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q180
Change-impact challenge: What does `PlanStepCapabilities.tools` mean relative to a Step's primary executable, and what runtime authority must still remain unavailable? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q181
Failure-diagnosis challenge: How are managed runtimes represented inside strict execution, and why must their original host filesystem topology never become sandbox semantics? Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q182
Cross-boundary synthesis: Distinguish the owners of authoring intent, executable behavior, resolved identity fingerprinting, authorization, execution, and execution evidence around `goon.lock`. Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q209
Hashmarks v0.13.0 emits exact flat verification membership and declares physical execution layout external. Who owns process isolation/grouping for those members, may Hashmarks smuggle an `isolated_process` or batching instruction into its admitted contract, and what must Oh-Goon preserve while choosing its own layout?
