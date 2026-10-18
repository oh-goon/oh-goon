# Section 9 — Evidence and observability ownership

## Q51
What durable stdout/stderr evidence may historical Game Tape expose for a failed Step, and what raw-log/source-line facts must remain explicitly unavailable rather than reconstructed?

## Q52
What may Scout own and do, what execution actions must it never own, and what happens to execution behavior if Scout is disabled or broken?

## Q53
What is `enforcer.yml`, which facts remain authoritative, and why must the receipt not become another state store or validation engine?

## Q54
When adding a new observability fact, how do you choose between Run/Game Tape projection, a typed domain observation owner, and Scout? What generic metrics shortcut is forbidden?

## Q55
What does an empty Execution Overview time-grid cell mean, and why must the UI not turn that absence into a new `NO_EXECUTION` status?

## Q188
Evidence-selection challenge: What durable stdout/stderr evidence may historical Game Tape expose for a failed Step, and what raw-log/source-line facts must remain explicitly unavailable rather than reconstructed? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q189
Negative-boundary challenge: What may Scout own and do, what execution actions must it never own, and what happens to execution behavior if Scout is disabled or broken? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q190
Change-impact challenge: What is `enforcer.yml`, which facts remain authoritative, and why must the receipt not become another state store or validation engine? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q191
Failure-diagnosis challenge: When adding a new observability fact, how do you choose between Run/Game Tape projection, a typed domain observation owner, and Scout? What generic metrics shortcut is forbidden? Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q192
Cross-boundary synthesis: What does an empty Execution Overview time-grid cell mean, and why must the UI not turn that absence into a new `NO_EXECUTION` status? Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q205
Hashmarks carries generation-bound negative repository evidence showing an edit target or hypothesis was wrong. Who owns that evidence, who decides execution retry/resume/environment recovery, and why may the negative evidence not become an autonomous recovery strategy?

## Q208
Game Tape shows Hashmarks repository evidence alongside Oh-Goon execution evidence. May Game Tape decide what work should run, retry it, or reinterpret the result? Explain Game Tape's projection role and which owners remain authoritative.


## Q214
A historical execution-governance plan contains CANDIDATE, GATED, BLOCKED, and externally blocked status text. Can an audit parser treat those markers as current repository defects without checking the checkout version and present implementation evidence? Explain how recency and authority affect that conclusion.

## Q218
An audit ranks candidates with `severity weight × impact factor` and assumes the ten highest numbers are therefore the ten most critical repository findings. Is that scoring formula sufficient authority for the conclusion? Explain how concrete evidence, currentness, uncertainty, and critical invariants should affect prioritization.
