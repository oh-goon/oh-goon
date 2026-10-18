# Section 8 — Frontend authority and concurrency

## Q46
Which execution/admission facts are backend-owned, what presentation work may the frontend perform, and what parallel browser state machine is forbidden?

## Q47
Who owns durable Run-review browser state for Run History and Game Tape, and how must URL state relate to canonical backend Run/Step/evidence identity?

## Q48
Describe the single frontend read-lifecycle ownership chain from resource mapping through concurrency, mounted refresh, and mutation invalidation. What duplicate refresh behavior is forbidden?

## Q49
A server-confirmed write finishes after the user navigates elsewhere. Does presentation relevance erase the write? Explain how mutation causality and commit observers are owned.

## Q50
Why is comparing only a location string unsafe for asynchronous presentation feedback, and how does `PresentationLocationAuthority` prevent the A→B→A ABA case?

## Q183
Evidence-selection challenge: Which execution/admission facts are backend-owned, what presentation work may the frontend perform, and what parallel browser state machine is forbidden? Identify the strongest repository evidence for the answer and explain why a plausible nearby source would be supporting evidence rather than the semantic owner.

## Q184
Negative-boundary challenge: Who owns durable Run-review browser state for Run History and Game Tape, and how must URL state relate to canonical backend Run/Step/evidence identity? Then state one plausible implementation or inference that would violate the ownership/authority boundary, and explain why it is forbidden.

## Q185
Change-impact challenge: Describe the single frontend read-lifecycle ownership chain from resource mapping through concurrency, mounted refresh, and mutation invalidation. What duplicate refresh behavior is forbidden? If the governing fact changed, identify the smallest authoritative surface that must change, one verification surface that must move with it, and one adjacent surface that should remain untouched.

## Q186
Failure-diagnosis challenge: A server-confirmed write finishes after the user navigates elsewhere. Does presentation relevance erase the write? Explain how mutation causality and commit observers are owned. Assume an implementation produces the opposite observable result. Diagnose which authority or invariant you would inspect first and what evidence would distinguish an owner defect from an observer symptom.

## Q187
Cross-boundary synthesis: Why is comparing only a location string unsafe for asynchronous presentation feedback, and how does `PresentationLocationAuthority` prevent the A→B→A ABA case? Connect this rule to the adjacent identity, lifecycle, execution, or evidence boundary that prevents a second owner from emerging.
