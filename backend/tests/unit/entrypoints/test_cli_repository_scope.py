from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_state import run_state_command
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.local_state import local_state


@pytest.mark.parametrize("command", ["exclude", "include"])
def test_repository_scope_cli_requires_displayed_instance_authority(command: str) -> None:
    argv = [command, "/repositories/project"]
    if command == "exclude":
        argv.extend(["--reason", "maintenance"])
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(argv)
    assert error.value.code == 2


def test_repository_scope_cli_transports_instance_and_rejects_stale_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(repository)
    runtime = SimpleNamespace(repositories=RepositoryService(state, RepositoryDiscoveryService()))
    identity = repository_subject_instance_id(repository)
    parser = build_parser()

    def invoke(command: str, expected: str) -> int | None:
        argv = [command, str(repository), "--expected-repository-instance-id", expected]
        if command == "exclude":
            argv.extend(["--reason", "maintenance"])
        return run_state_command(runtime, parser.parse_args(argv))

    assert invoke("exclude", "stale-instance") == 2
    assert "Repository instance changed" in capsys.readouterr().out
    assert state.exclusions() == []
    assert invoke("exclude", identity) == 0
    assert "EXCLUDED" in capsys.readouterr().out
    assert len(state.exclusions()) == 1
    assert invoke("include", "stale-instance") == 2
    assert "Repository instance changed" in capsys.readouterr().out
    assert len(state.exclusions()) == 1
    assert invoke("include", identity) == 0
    assert "INCLUDED" in capsys.readouterr().out
    assert state.exclusions() == []
