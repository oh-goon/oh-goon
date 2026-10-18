from __future__ import annotations

from pathlib import Path

from oh_goon import container_entrypoint


def _valid_env(monkeypatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("OH_GOON_CONTAINER", "1")
    monkeypatch.setenv("OH_GOON_DB", str(state / "oh-goon.db"))
    monkeypatch.setattr(container_entrypoint, "built_ui_available", lambda: True)
    return state


def test_container_configuration_accepts_writable_persistent_directory(
    monkeypatch, tmp_path: Path
) -> None:
    _valid_env(monkeypatch, tmp_path)
    assert container_entrypoint.container_configuration_issues() == ()


def test_container_configuration_rejects_missing_state_directory(
    monkeypatch, tmp_path: Path
) -> None:
    state = _valid_env(monkeypatch, tmp_path)
    state.rmdir()
    assert container_entrypoint.container_configuration_issues() == (
        f"container state directory does not exist: {state}",
    )


def test_container_configuration_rejects_unwritable_state_directory(
    monkeypatch, tmp_path: Path
) -> None:
    state = _valid_env(monkeypatch, tmp_path)
    original_access = container_entrypoint.os.access
    monkeypatch.setattr(
        container_entrypoint.os,
        "access",
        lambda path, mode: False if Path(path) == state else original_access(path, mode),
    )
    assert container_entrypoint.container_configuration_issues() == (
        f"container state directory is not writable: {state}",
    )


def test_container_startup_rejects_invalid_league_preferences_before_uvicorn(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = _valid_env(monkeypatch, tmp_path)
    league = state / "league.yml"
    league.write_text("version: 1\nfolders: [\n", encoding="utf-8")
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setattr(container_entrypoint, "check_local_state_for_startup", lambda: 0)
    assert container_entrypoint.main() == 2
    output = capsys.readouterr().out
    assert "Cannot read League preferences" in output
    assert f"League preferences: {league}" in output
