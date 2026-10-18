import types
from pathlib import Path

from oh_goon.cli import main


def test_start_requires_bundled_ui_without_starting_server(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr("oh_goon.cli_start.built_ui_available", lambda: False)

    assert main(["start"]) == 2

    output = capsys.readouterr().out
    assert "bundled oh-goon UI is not present" in output
    assert "make dev" in output


def test_start_serves_one_loopback_app_and_reports_saved_league(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.db"
    league_root = tmp_path / "repos"
    league_root.mkdir()
    monkeypatch.setenv("OH_GOON_DB", str(database))

    from oh_goon.runtime.league_folders import LeagueFolderStore

    LeagueFolderStore().replace((league_root,))
    monkeypatch.setattr("oh_goon.cli_start.built_ui_available", lambda: True)
    monkeypatch.setattr(
        "oh_goon.cli_start.start_inprocess_readiness_monitor",
        lambda **kwargs: (kwargs["on_ready"](), types.SimpleNamespace(cancel=lambda: None))[1],
    )
    observed: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, app: str, *, host: str, port: int, reload: bool) -> None:
            observed.update(app=app, host=host, port=port, reload=reload)

    class FakeServer:
        def __init__(self, _config: object) -> None:
            self.should_exit = False

        def run(self) -> None:
            return None

    monkeypatch.setattr("uvicorn.Config", FakeConfig)
    monkeypatch.setattr("uvicorn.Server", FakeServer)

    assert main(["start"]) == 0
    assert observed == {
        "app": "oh_goon.api.app:app",
        "host": "127.0.0.1",
        "port": 8787,
        "reload": False,
    }
    output = capsys.readouterr().out
    assert "League folders: 1 saved on this computer" in output
    assert "http://127.0.0.1:8787" in output
    assert str(LeagueFolderStore().path) in output
