# agents/agentic-review-repair-loop

Compose four Skill reviews, adjudication, scoped repair and native project checks.

**Status:** runnable with prerequisites. **Effects:** Orders four reviews, adjudication, admitted repair and native project checks.

**Prerequisites:** Pinned review Skills, local adjudication/repair Skills, a configured model provider and explicit executable regression/verification scripts. Repairs need admitted write roots and fresh authorization.

```sh
oh-goon init --template agents/agentic-review-repair-loop --kind goon --output ./workflow --draft
```

This explicit draft is NOT_READY and has no lock. Complete `target.repository` and required inputs or Agent/repair fields, then prepare it. For a complete first run, use [normal initialization](../../../README.md).

Set `target.repository` and caller choices in `workflow/run.yml`. [`goon.yml`](goon.yml) owns the input contract; omitted choices keep its defaults. Then [inspect, validate, lock and run](../../../README.md#first-local-python-run).

**Result:** Typed review/adjudication/proposal records and native apply/check outcomes.

The Atomic Goons own the input contracts. Required inputs are `repo`, `regression_command` and `verification_command`; `goal`, `acceptance`, `adjudication_skill` and `repair_skill` keep their defaults. Shared inputs use explicit `pass:` recipients, just like other Composite Goons.

Select registered Skills and a provider, and set the review-pass, write-root and deadline limits in `run.yml`. Start each effectful Run with `oh-goon run ./workflow/run.yml --authorize-repair`. See [setup and execution](../../../../examples/agentic-review-repair-loop/README.md) for a complete invocation.

Reviews share one frozen candidate. Findings feed adjudication, repair and regression checks; each changed candidate restarts at reviewer one. Completion requires fresh CLEAN records from every reviewer and successful final verification on that candidate. Each phase uses the ordinary Run lifecycle; convergence does not confer APP certification.

A declared ordinary regression failure can start another fresh review when its
native process has settled safely and complete, bounded stdout feedback is
available. Review, adjudication and repair receive that candidate-bound feedback;
the failed Run remains failed history. Missing feedback, uncertain cleanup,
cancellation, timeouts and undeclared failures stop the campaign. A CLEAN review
cannot override a known failed regression on the same candidate.

Recovery consumes the original deadline and pass/Run budgets. The next repair
uses fresh repository evidence and current file preimages, then must pass the
unchanged regression command, fresh review and final verification. Frozen Skills,
harness selection and allowed write roots continue to apply. See the
[regression script contract](../regression/README.md) for exit codes and feedback.

Copy this Composite into a project-owned catalog to replace, append or reorder reviewers. Edit the ordered `phase: review` imports and their `with.skill` bindings. The controller accepts any registered Skill; native Git owns external Skill checkout and pinning.
