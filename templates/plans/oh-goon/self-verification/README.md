# oh-goon/self-verification

Canonical fast self-verification Plan for one exact initialized Oh-Goon source revision.

This is a normal verification Plan, not a privileged verifier. `make init` owns dependency/interpreter materialization once. Self verification consumes that initialized repository and must not reacquire dependencies. Plan resolution, locking, execution evidence, cancellation and Game Tape remain normal Oh-Goon machinery.

The fast boundary covers P0 execution integrity, repository tooling, semantic architecture ownership, generated contracts, integration behavior, and repository-understanding certification. Full `oh-goon self certify` selects the lean `oh-goon/app-certification` Plan; `make verify` owns broader diagnostics.
