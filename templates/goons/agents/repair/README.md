# agents/repair

Propose a source-bound batch and apply it through an admitted native Step.

**Status:** runnable with prerequisites. **Effects:** Runs the declared model/tool/native phase with its declared repository access and disclosure recipient.

**Prerequisites:** Explicit repository/Skill source roots, a permitted acceptance profile and the configured provider/native agent required by this Goon. Project check scripts must exist when selected; effectful work still needs fresh original admission.

**What to edit / settings owner:** select caller values in your workflow `run.yml`. [`goon.yml`](goon.yml) owns input types/defaults, Steps, requirements, effects and outputs. Select this capability through a Plan; use [the common setup and customization guide](../../../README.md) for inspect → validate → lock → run.

**Expected outputs:** Typed Agent records, proposal data or native project check results, according to the selected phase. Agent claims do not become verification truth.

**Failures:** invalid bindings reject before work; native failures, missing capabilities and incomplete execution retain their actual Step/Run status. Configuration validation alone proves no successful effect.

**Customize:** change caller inputs in `run.yml`; use ordinary `with:`/`pass:` bindings and ordered imports in a project Plan/Composite for structural changes. Native tools keep their own configuration.

Uses `repair_skill`, `repo`, `goal` and `acceptance`. The Agent only proposes. The ordinary native `oh-goon agent apply` Step validates the entire batch before writing. `@admitted` and `@candidate` are replaced by the existing repair owner with the invocation's explicit write roots and captured candidate identity before Run admission. These markers never authorize standalone apply. Create, update and delete require exact preimages; publication failures stop as incomplete.
