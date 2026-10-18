from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.release.runtime_journey import (
    prepare_acceptance_repository,
    run_acceptance_goon,
)
from oh_goon.repository_tooling.scout_http_load_probe import (
    ROOT,
    SCOUT_HTTP_ACCESS_TOKEN,
    _get_json,
    _journal_revision,
    _port,
    _start_server,
    _stop_server,
    _wait_json,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import scout_analytical_database_path


@dataclass(frozen=True, slots=True)
class ScoutCorruptionRecoveryResult:
    canonical_revision_before_corruption: int
    canonical_revision_after_recovery: int
    processed_revision_after_recovery: int
    retained_runs_after_recovery: int
    retained_steps_after_recovery: int
    second_execution_status: str
    second_execution_outcome: str
    second_game_tape_contains_acceptance_step: bool
    retained_runs_after_second_execution: int
    retained_steps_after_second_execution: int
    final_lag_revisions: int
    blocked_outbound_attempts: int


def _run_real_goon(root: Path, state: Path, league: Path, repo: Path, guard_log: Path) -> str:
    port = _port()
    base = f"http://127.0.0.1:{port}"
    server = _start_server(
        root=root, state=state, league=league, port=port, loopback_guard_log=guard_log
    )
    try:
        _wait_json(f"{base}/api/health", process=server)
        return run_acceptance_goon(
            base_url=base,
            server_repository_path=str(repo.resolve()),
            access_token=SCOUT_HTTP_ACCESS_TOKEN,
        )
    finally:
        _stop_server(server)


def run_corruption_recovery_certification(*, root: Path = ROOT) -> ScoutCorruptionRecoveryResult:
    with TemporaryDirectory(prefix="oh-goon-scout-corruption-recovery-") as directory:
        temp = Path(directory)
        state = temp / "state" / "oh-goon.db"
        league = temp / "state" / "league.yml"
        repo = temp / "repositories" / "acceptance-repo"
        guard_log = temp / "network-audit.jsonl"
        prepare_acceptance_repository(repo)
        _run_real_goon(root, state, league, repo, guard_log)
        database = LocalDatabase(state)
        revision_before = _journal_revision(database)
        if revision_before < 1:
            raise RuntimeError("corruption recovery requires real canonical Journal truth")

        sidecar = scout_analytical_database_path(state)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_bytes(b"this is intentionally not a sqlite database\n")
        port = _port()
        base = f"http://127.0.0.1:{port}"
        server = _start_server(
            root=root,
            state=state,
            league=league,
            port=port,
            loopback_guard_log=guard_log,
        )
        try:
            _wait_json(f"{base}/api/health", process=server)
            recovered = _get_json(f"{base}/api/scout/health")
            revision_after = _journal_revision(LocalDatabase(state))
            if revision_after != revision_before:
                raise RuntimeError(
                    "Scout corruption recovery changed canonical durable Journal revision"
                )
            if recovered.get("processed_journal_revision") != revision_before:
                raise RuntimeError("Scout did not rebuild to the canonical Journal revision")
            if recovered.get("lag_revisions") != 0:
                raise RuntimeError("Scout remained behind Journal after corruption recovery")
            second_run = run_acceptance_goon(
                base_url=base,
                server_repository_path=str(repo.resolve()),
                access_token=SCOUT_HTTP_ACCESS_TOKEN,
            )
            fleet = _get_json(f"{base}/api/fleet/runs/{second_run}")
            final_health = _get_json(f"{base}/api/scout/health")
        finally:
            _stop_server(server)
        attempts = blocked_network_attempts(guard_log)
        if attempts:
            raise RuntimeError(
                f"Scout corruption recovery attempted outbound network: {attempts[:5]!r}"
            )
    run = fleet.get("run") if isinstance(fleet, dict) else None
    status = run.get("status") if isinstance(run, dict) else None
    outcome = fleet.get("outcome") if isinstance(fleet, dict) else None
    game_tape_ok = "acceptance-step" in json.dumps(fleet.get("game_tape"))
    if status != "COMPLETED" or outcome != "SUCCESS" or not game_tape_ok:
        raise RuntimeError("real execution/Game Tape failed after Scout corruption recovery")
    return ScoutCorruptionRecoveryResult(
        canonical_revision_before_corruption=revision_before,
        canonical_revision_after_recovery=revision_after,
        processed_revision_after_recovery=int(recovered["processed_journal_revision"]),
        retained_runs_after_recovery=int(recovered["retained_run_observations"]),
        retained_steps_after_recovery=int(recovered["retained_step_observations"]),
        second_execution_status=str(status),
        second_execution_outcome=str(outcome),
        second_game_tape_contains_acceptance_step=game_tape_ok,
        retained_runs_after_second_execution=int(final_health["retained_run_observations"]),
        retained_steps_after_second_execution=int(final_health["retained_step_observations"]),
        final_lag_revisions=int(final_health["lag_revisions"]),
        blocked_outbound_attempts=0,
    )


def main() -> int:
    print(json.dumps(asdict(run_corruption_recovery_certification()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
