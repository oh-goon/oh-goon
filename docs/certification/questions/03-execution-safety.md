# Section 3 — Execution safety and flow contracts

## Q11
Where does repository/path isolation belong, and how should it be treated architecturally?

## Q12
Trace the APP verification front door from `make certify` to APP Plan execution, and explain where unused strict executor authority is stripped. Identify the orchestration owner.

## Q13
What does `make certify` never do to host configuration or strict executor authority? What is the separate explicit strict-executor host-setup command?

## Q14
Describe the one-way API contract propagation path from backend authority to frontend consumption.

## Q15
What is the public Schedule model, and what does the scheduler own versus not own?

## Q158
Evidence-selection challenge: Where does repository/path isolation belong, and how should it be treated architecturally? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q159
Negative-boundary challenge: Trace the APP verification front door from `make certify` to APP Plan execution, and explain where unused strict executor authority is stripped. Identify the orchestration owner. Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q160
Change-impact challenge: What does `make certify` never do to host configuration or strict executor authority? What is the separate explicit strict-executor host-setup command? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q161
Failure-diagnosis challenge: Describe the one-way API contract propagation path from backend authority to frontend consumption. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q162
Cross-boundary synthesis: What is the public Schedule model, and what does the scheduler own versus not own? Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.

## Q203
Hashmarks identifies a repository test or bounded verification surface as relevant. Does that selection authorize or execute the verification? Name the owner of verification selection, the owner of process execution/result authority, and the failure if one plane silently takes the other's role.

## Q207
A physical Oh-Goon batch created from a frozen Hashmarks verification selection times out and Oh-Goon bisects it. What is allowed to change, what must remain exactly conserved, and why must timeout refinement never become a second verification-membership selector?

## Q212
Suppose a Hashmarks downstream verification contract or selection envelope starts carrying timeout, retry count, worker concurrency, machine placement, scheduling, or other physical execution-layout instructions. Should Oh-Goon honor those fields? Explain which plane owns those policies and what admission behavior is required.
