from __future__ import annotations


def require_successful_repository_execution(
    detail: dict[str, object],
    repository_ids: list[str],
    *,
    label: str,
) -> None:
    """Require exact repository-attributed successful work in Game Tape."""

    game_tape = detail.get("game_tape")
    repository_facts = game_tape.get("repositories") if isinstance(game_tape, dict) else None
    if not isinstance(repository_facts, list):
        raise RuntimeError(f"{label} lost repository-attributed Game Tape evidence")
    observed = _repository_facts(repository_facts)
    if set(observed) != set(repository_ids):
        raise RuntimeError(
            f"{label} did not preserve exact selected Team execution: "
            f"expected={sorted(repository_ids)!r} observed={sorted(observed)!r}"
        )
    for repository_id in repository_ids:
        _require_passed_steps(observed[repository_id], repository_id, label=label)


def require_repository_bound_step_started(
    detail: dict[str, object],
    repository_ids: list[str],
    *,
    label: str,
) -> None:
    """Require at least one durable STEP_STARTED fact for every selected repository."""

    game_tape = detail.get("game_tape")
    play_by_play = game_tape.get("play_by_play") if isinstance(game_tape, dict) else None
    if not isinstance(play_by_play, list):
        raise RuntimeError(f"{label} lost durable Game Tape events")
    started = {
        event.get("repository_id")
        for event in play_by_play
        if isinstance(event, dict)
        and event.get("event_type") == "STEP_STARTED"
        and isinstance(event.get("repository_id"), str)
    }
    missing = set(repository_ids) - started
    if missing:
        raise RuntimeError(
            f"{label} lacks repository-bound durable STEP_STARTED evidence: {sorted(missing)!r}"
        )


def _repository_facts(rows: list[object]) -> dict[str, dict[str, object]]:
    observed: dict[str, dict[str, object]] = {}
    for fact in rows:
        repository = fact.get("repository") if isinstance(fact, dict) else None
        repository_id = repository.get("repository_id") if isinstance(repository, dict) else None
        if isinstance(repository_id, str) and isinstance(fact, dict):
            observed[repository_id] = fact
    return observed


def _require_passed_steps(fact: dict[str, object], repository_id: str, *, label: str) -> None:
    steps = fact.get("steps")
    if fact.get("status") != "PASSED" or not isinstance(steps, list) or not steps:
        raise RuntimeError(
            f"{label} did not prove successful physical work for Team {repository_id}"
        )
    if any(not isinstance(step, dict) or step.get("status") != "PASSED" for step in steps):
        raise RuntimeError(f"{label} contains non-passed Step evidence for Team {repository_id}")


def require_preview_counts(
    preview: dict[str, object], *, ready: int, blocked: int, label: str
) -> list[dict[str, object]]:
    """Return exact Team rows only when preview cardinality matches the scenario."""

    if preview.get("ready_count") != ready or preview.get("blocked_count") != blocked:
        raise RuntimeError(
            f"{label} did not preserve {ready} READY / {blocked} BLOCKED Team semantics: {preview!r}"
        )
    teams = preview.get("teams")
    if not isinstance(teams, list) or any(not isinstance(team, dict) for team in teams):
        raise RuntimeError(f"{label} did not return Team evidence")
    return teams


def require_preview_work(
    team: dict[str, object],
    *,
    applicability: str,
    disposition: str,
    label: str,
    reason_contains: str | None = None,
) -> None:
    """Require one projected import/Step disposition without redefining Team readiness."""

    projected = team.get("preview")
    imports = projected.get("imports") if isinstance(projected, dict) else None
    steps = projected.get("steps") if isinstance(projected, dict) else None
    first_import = imports[0] if isinstance(imports, list) and imports else None
    first_step = steps[0] if isinstance(steps, list) and steps else None
    if not isinstance(first_import, dict) or not isinstance(first_step, dict):
        raise RuntimeError(f"{label} lost projected import/Step evidence")
    if (
        first_import.get("applicability") != applicability
        or first_step.get("disposition") != disposition
    ):
        raise RuntimeError(f"{label} expected {applicability}/{disposition} import/Step evidence")
    if reason_contains is not None and reason_contains not in str(first_import.get("reason")):
        raise RuntimeError(f"{label} lost applicability reason: {reason_contains}")
