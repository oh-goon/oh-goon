from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)


def _write_progress_contract_fixture(root: Path, *, progress_protocol: str) -> None:
    application = root / "backend/src/oh_goon/application.py"
    application.parent.mkdir(parents=True, exist_ok=True)
    application.write_text(
        "class ApplicationRuntime:\n    runs: FleetRunControlPlane\n",
        encoding="utf-8",
    )
    projection = root / "backend/src/oh_goon/services/live_projection.py"
    projection.parent.mkdir(parents=True, exist_ok=True)
    projection.write_text(
        "class GameTapeLiveOutputBatch:\n"
        "    pass\n\n"
        "class FleetRunDetail:\n"
        "    game_tape = object()\n\n"
        "def project_transient_output(store, detail: FleetRunDetail, *, after: int) -> GameTapeLiveOutputBatch:\n"
        "    _ = detail.game_tape\n"
        "    return store.snapshot(after)\n",
        encoding="utf-8",
    )
    control_plane = root / "backend/src/oh_goon/services/fleet_run_control_plane.py"
    control_plane.write_text(
        "from oh_goon.services.live_projection import project_transient_output\n\n"
        "class GameTapeLiveOutputBatch:\n"
        "    pass\n\n"
        "class FleetRunControlPlane:\n"
        "    def observe_transient_output(self, run_id: str, *, after: int = 0) -> GameTapeLiveOutputBatch:\n"
        "        return project_transient_output(self._live_output, self.get(run_id), after=after)\n",
        encoding="utf-8",
    )
    plan_cli = root / "backend/src/oh_goon/cli_runtime_plans.py"
    plan_cli.parent.mkdir(parents=True, exist_ok=True)
    plan_cli.write_text(
        "\n".join(
            (
                "class ApplicationRuntime:",
                "    pass",
                "",
                "def render(runtime: ApplicationRuntime):",
                "    runtime.runs.observe_transient_output('run-1', after=0)",
                'MILESTONE = "Observed activity: Goon"',
                'HEARTBEAT = "Certification working"',
                f'PROGRESS_PROTOCOL = "{progress_protocol}"',
                "",
            )
        ),
        encoding="utf-8",
    )


def test_certification_progress_architecture_accepts_semantics_without_control_flow_choreography(
    tmp_path: Path,
) -> None:
    _write_progress_contract_fixture(tmp_path, progress_protocol="OH_GOON_PROGRESS_FD")

    assert check_project_supply_contract(tmp_path) == []


def test_certification_progress_architecture_still_requires_progress_protocol_identity(
    tmp_path: Path,
) -> None:
    _write_progress_contract_fixture(tmp_path, progress_protocol="PRESENTATION_FD")

    violations = check_project_supply_contract(tmp_path)

    assert len(violations) == 1
    assert "OH_GOON_PROGRESS_FD" in violations[0].message
