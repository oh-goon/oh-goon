# Section 1 — Repository ownership and navigation

Answer each question with the owner, the relevant boundary, and repository evidence you found.

## Q01
Where does a change to an API route or response model belong? Include where focused backend tests belong and what the API layer must not own. If that boundary changes, identify the contract/test surfaces likely affected and one architectural area that should remain untouched.

## Q02
Where should a change to Goon/Plan resolution start? When is changing a core model appropriate instead? Predict which resolver/contract tests should be reconsidered and which runtime responsibility should remain untouched if only resolution semantics change.

## Q03
You need to change a user-facing frontend capability. Which file should you open first, and why? Predict the local test surface that should move with the change and name a backend/generated-contract area that should remain untouched when no contract fact changes.

## Q04
When is a frontend change frontend-local, and what should happen if the generated backend/frontend contract boundary changes? Distinguish the tests/generated artifacts that become affected from authored surfaces that should not be edited as a workaround.

## Q05
Where are shipped Goon and Plan artifacts owned? Describe the package shape and the identity rule for a shipped Goon.

## Q148
Evidence-selection challenge: Where does a change to an API route or response model belong? Include where focused backend tests belong and what the API layer must not own. If that boundary changes, identify the contract/test surfaces likely affected and one architectural area that should remain untouched. Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q149
Negative-boundary challenge: Where should a change to Goon/Plan resolution start? When is changing a core model appropriate instead? Predict which resolver/contract tests should be reconsidered and which runtime responsibility should remain untouched if only resolution semantics change. Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q150
Change-impact challenge: You need to change a user-facing frontend capability. Which file should you open first, and why? Predict the local test surface that should move with the change and name a backend/generated-contract area that should remain untouched when no contract fact changes. If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q151
Failure-diagnosis challenge: When is a frontend change frontend-local, and what should happen if the generated backend/frontend contract boundary changes? Distinguish the tests/generated artifacts that become affected from authored surfaces that should not be edited as a workaround. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q152
Cross-boundary synthesis: Where are shipped Goon and Plan artifacts owned? Describe the package shape and the identity rule for a shipped Goon. Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q210
Oh-Goon needs a new repository-intelligence feature such as impact ranking, context selection, or verification relevance. Should it be prototyped inside an Oh-Goon Hashmarks adapter? State the required product boundary and the allowed integration path.

## Q211
A coding task asks what source change should be made. Should Hashmarks decide the solution because it owns repository evidence, or should Oh-Goon decide because it owns certification? Identify the solution owner and explain why neither control plane may absorb it.

## Q213
A repository audit plan proposes creating an importable `scripts/audit/` Python package because `scripts/` owns repository tooling. Is that the correct executable-Python ownership? State where repository Python belongs, how it is invoked, and what `scripts/` is allowed to contain.

## Q216
A complexity scan marks every module with at least 30 inbound imports and more than 1,500 lines as a "God Object" that should be split. Is that enough evidence for decomposition in Oh-Goon? Explain the required responsibility-first review and what must remain local even when metrics are high.
