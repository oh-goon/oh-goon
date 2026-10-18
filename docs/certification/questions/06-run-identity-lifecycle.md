# Section 6 — Run identity and lifecycle authority

## Q36
A failed execution needs recovery. Which owner may start an exact-target rerun or placement readmission, which owner handles uncertain/partial repository effects, and what must Game Tape do instead?

## Q37
What authority does the process-local API access capability grant, what does it explicitly not create, and what identity rule applies to rerun/readmission child Runs?

## Q38
After a backend restart, how are a still-live execution owner, an abandoned interrupted cancellation, and another abandoned non-terminal Run handled?

## Q39
What does the Final Whistle `cleanPass` mean, who derives it, and what must React not calculate or imply from it?

## Q40
At runtime, may execution rediscover/reparse Plan or Goon source? Identify the immutable representation runtime must receive and the ownership boundary this preserves.

## Q173
Evidence-selection challenge: A failed execution needs recovery. Which owner may start an exact-target rerun or placement readmission, which owner handles uncertain/partial repository effects, and what must Game Tape do instead? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q174
Negative-boundary challenge: What authority does the process-local API access capability grant, what does it explicitly not create, and what identity rule applies to rerun/readmission child Runs? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q175
Change-impact challenge: After a backend restart, how are a still-live execution owner, an abandoned interrupted cancellation, and another abandoned non-terminal Run handled? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q176
Failure-diagnosis challenge: What does the Final Whistle `cleanPass` mean, who derives it, and what must React not calculate or imply from it? Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q177
Cross-boundary synthesis: At runtime, may execution rediscover/reparse Plan or Goon source? Identify the immutable representation runtime must receive and the ownership boundary this preserves. Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q206
A Hashmarks downstream contract, selection envelope/provenance, and selected-membership identity are stable producer-side repository facts. Are any of those the Oh-Goon execution-plan, Fleet Run, or physical-attempt identity? Explain which identities must remain distinct and why resume/Game Tape must preserve rather than collapse them.

