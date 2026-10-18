# Section 10 — Cross-repository and security boundaries

## Q56
During a Shared Run, what happens to each selected repository's root `goon.yml`, and which source owns executable intent for that run?

## Q57
For `plan run`, what does `--all-repositories` mean and what must an omitted repository selector never mean?

## Q58
Do `oh-goon team validate` and `oh-goon enforcer run` apply a separate local quality baseline? Explain which source and preview/admission owner they use.

## Q59
Does League scanning establish semantic CI/CD readiness for every discovered Team? Explain the boundary between structural discovery and explicit validation/admission.

## Q60
If retained content is physically deduplicated by a content hash, does knowing another repository's hash or retained reference authorize access? Identify the authorization identity that must still be proved.

## Q193
Evidence-selection challenge: During a Shared Run, what happens to each selected repository's root `goon.yml`, and which source owns executable intent for that run? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q194
Negative-boundary challenge: For `plan run`, what does `--all-repositories` mean and what must an omitted repository selector never mean? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q195
Change-impact challenge: Do `oh-goon team validate` and `oh-goon enforcer run` apply a separate local quality baseline? Explain which source and preview/admission owner they use. If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q196
Failure-diagnosis challenge: Does League scanning establish semantic CI/CD readiness for every discovered Team? Explain the boundary between structural discovery and explicit validation/admission. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q197
Cross-boundary synthesis: If retained content is physically deduplicated by a content hash, does knowing another repository's hash or retained reference authorize access? Identify the authorization identity that must still be proved. Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q204
Hashmarks reports that repository A depends on repository B or that they share an input. Does that static project topology schedule either repository? Distinguish cross-repository evidence ownership from Fleet membership/Plan/Schedule execution ownership.


## Q217
A proposed Hashmarks boundary leak detector flags raw words such as `CodeMap` or `verification-selection` anywhere outside `integrations/hashmarks*`. Is token occurrence a sound proxy for forbidden ownership? Explain what the existing boundary actually inspects and why legitimate consumers, documentation, and tests may still use the vocabulary.
