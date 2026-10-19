# agents/agentic-review-repair-loop

Compose four Skill reviews, adjudication, scoped repair and native project checks.

The Atomic Goons own the input contracts. Required inputs are `repo`, `regression_command` and `verification_command`; `goal`, `acceptance`, `adjudication_skill` and `repair_skill` keep their defaults. Shared inputs use explicit `pass:` recipients, just like other Composite Goons.

Import from a verification Plan. Select registered Skills and a provider, then run with explicit review-pass, write-root and deadline limits. See [setup and execution](../../../../examples/agentic-review-repair-loop/README.md) for a complete invocation.

Reviews share one frozen candidate. Findings feed adjudication, repair and regression checks; each changed candidate restarts at reviewer one. Completion requires fresh CLEAN records from every reviewer and successful final verification on that candidate. Each phase uses the ordinary Run lifecycle; convergence does not confer APP certification.

Copy this Composite into a project-owned catalog to replace, append or reorder reviewers. Edit the ordered `phase: review` imports and their `with.skill` bindings. The controller accepts any registered Skill; native Git owns external Skill checkout and pinning.
