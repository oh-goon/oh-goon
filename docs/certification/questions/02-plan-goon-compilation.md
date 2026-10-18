# Section 2 — Plan/Goon compilation contract

## Q06
What is the authoritative identity of a shipped Goon across authoring, compilation, lookup, and runtime?

## Q07
Explain the parse-once rule for Plan/Goon YAML. Name the repeated work that must not be reintroduced below the parser boundary.

## Q08
What may a Plan contain, and which executable fields are forbidden in a Plan?

## Q09
Describe the compilation chain from authored YAML to runtime execution.

## Q10
What is the rule for generated artifacts when the generated OpenAPI/frontend contract is wrong?

## Q153
Evidence-selection challenge: What is the authoritative identity of a shipped Goon across authoring, compilation, lookup, and runtime? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q154
Negative-boundary challenge: Explain the parse-once rule for Plan/Goon YAML. Name the repeated work that must not be reintroduced below the parser boundary. Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q155
Change-impact challenge: What may a Plan contain, and which executable fields are forbidden in a Plan? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q156
Failure-diagnosis challenge: Describe the compilation chain from authored YAML to runtime execution. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q157
Cross-boundary synthesis: What is the rule for generated artifacts when the generated OpenAPI/frontend contract is wrong? Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.
