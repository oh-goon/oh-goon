from oh_goon.core.product_identity import stable_id


def repository_execution_id(fleet_run_id: str, repository_id: str) -> str:
    return stable_id(
        "game-tape-repository",
        {"fleet_run_id": fleet_run_id, "repository_id": repository_id},
    )


def step_execution_id(fleet_run_id: str, repository_id: str, step_id: str) -> str:
    return stable_id(
        "game-tape-step",
        {"fleet_run_id": fleet_run_id, "repository_id": repository_id, "step_id": step_id},
    )
