from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.scout_observer_contract import (
    check_scout_observer_contract,
)


def _python(path: Path, text: str = "import pathlib\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _base_package(root: Path) -> Path:
    package = root / "oh_goon"
    _python(package / "scout/__init__.py", "")
    _python(
        package / "application.py", "from oh_goon.scout.service import ScoutService\n"
    )
    _python(
        package / "api/routes/observer.py",
        "from oh_goon.scout.service import ScoutService\n",
    )
    return package


def test_scout_internal_filename_rename_does_not_change_architecture(
    tmp_path: Path,
) -> None:
    package = _base_package(tmp_path)
    first = package / "scout/evidence.py"
    _python(first, "from oh_goon.runtime.database import LocalDatabase\n")
    assert check_scout_observer_contract(package) == []
    first.rename(package / "scout/observations.py")
    assert check_scout_observer_contract(package) == []


def test_scout_package_membership_not_filename_defines_owner(tmp_path: Path) -> None:
    package = _base_package(tmp_path)
    _python(
        package / "scout/custom.py",
        "from oh_goon.services.fleet_run_execution import FleetRunExecutionService\n",
    )
    issues = check_scout_observer_contract(package)
    assert any(
        "Scout must not depend on control owner" in issue.message for issue in issues
    )
    (package / "scout/custom.py").unlink()
    _python(package / "runtime/scout_custom.py", "import pathlib\n")
    assert check_scout_observer_contract(package) == []


def test_module_outside_scout_cannot_depend_on_scout_by_using_neutral_filename(
    tmp_path: Path,
) -> None:
    package = _base_package(tmp_path)
    _python(
        package / "runtime/custom.py",
        "from oh_goon.scout.store import ScoutObservationStore\n",
    )
    issues = check_scout_observer_contract(package)
    assert any(
        "execution owners must not depend on Scout" in issue.message for issue in issues
    )
