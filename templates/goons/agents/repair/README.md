# agents/repair

Propose a source-bound batch and apply it through an admitted native Step.

Uses `repair_skill`, `repo`, `goal` and `acceptance`. The Agent only proposes. The ordinary native `oh-goon agent apply` Step validates the entire batch before writing. `@admitted` and `@candidate` are replaced by the existing repair owner with the invocation's explicit write roots and captured candidate identity before Run admission. These markers never authorize standalone apply. Create, update and delete require exact preimages; publication failures stop as incomplete.
