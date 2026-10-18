from pathlib import Path

from oh_goon.repository_tooling.architecture import repository_understanding_qualification_boundary_contract as contract


def test_repository_understanding_qualification_boundary_current_tree() -> None:
    assert contract.check_repository_understanding_qualification_boundary_contract() == []


def test_hashmarks_adapter_cannot_import_ru_grading_authority(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "oh_goon"
    integrations = package / "integrations" / "hashmarks"
    integrations.mkdir(parents=True)
    adapter = integrations / "banana.py"
    adapter.write_text(
        "from oh_goon.repository_tooling.repository_understanding_policy import grade_candidate\n",
        encoding="utf-8",
    )
    repository_tooling = package / "repository_tooling"
    repository_tooling.mkdir()
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "HASHMARKS_INTEGRATION_ROOT", integrations)
    monkeypatch.setattr(contract, "REPOSITORY_TOOLING_ROOT", repository_tooling)

    violations = contract.check_repository_understanding_qualification_boundary_contract()

    assert any(item.path == adapter and "must not import Repository Understanding" in item.message for item in violations)


def test_hashmarks_adapter_cannot_address_secret_answer_tree(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "oh_goon"
    integrations = package / "integrations" / "hashmarks"
    integrations.mkdir(parents=True)
    adapter = integrations / "banana.py"
    adapter.write_text('ANSWER_ROOT = "docs/certification/answers"\n', encoding="utf-8")
    repository_tooling = package / "repository_tooling"
    repository_tooling.mkdir()
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "HASHMARKS_INTEGRATION_ROOT", integrations)
    monkeypatch.setattr(contract, "REPOSITORY_TOOLING_ROOT", repository_tooling)

    violations = contract.check_repository_understanding_qualification_boundary_contract()

    assert any(item.path == adapter and "SECRET answer truth" in item.message for item in violations)


def test_hashmarks_package_boundary_is_recursive_and_filename_independent(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "oh_goon"
    integrations = package / "integrations" / "hashmarks"
    nested = integrations / "nested"
    nested.mkdir(parents=True)
    adapter = nested / "anything.py"
    adapter.write_text(
        "from oh_goon.repository_tooling.repository_understanding_policy import grade_candidate\n",
        encoding="utf-8",
    )
    repository_tooling = package / "repository_tooling"
    repository_tooling.mkdir()
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "HASHMARKS_INTEGRATION_ROOT", integrations)
    monkeypatch.setattr(contract, "REPOSITORY_TOOLING_ROOT", repository_tooling)

    violations = contract.check_repository_understanding_qualification_boundary_contract()

    assert any(item.path == adapter and "must not import Repository Understanding" in item.message for item in violations)


def test_hashmarks_package_secret_rule_is_filename_independent(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "oh_goon"
    integrations = package / "integrations" / "hashmarks"
    integrations.mkdir(parents=True)
    adapter = integrations / "foo.py"
    adapter.write_text('ANSWER_ROOT = "docs/certification/answers"\n', encoding="utf-8")
    repository_tooling = package / "repository_tooling"
    repository_tooling.mkdir()
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "HASHMARKS_INTEGRATION_ROOT", integrations)
    monkeypatch.setattr(contract, "REPOSITORY_TOOLING_ROOT", repository_tooling)

    violations = contract.check_repository_understanding_qualification_boundary_contract()

    assert any(item.path == adapter and "SECRET answer truth" in item.message for item in violations)


def test_ru_qualification_owner_cannot_import_hashmarks_semantics(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "oh_goon"
    integrations = package / "integrations" / "hashmarks"
    integrations.mkdir(parents=True)
    repository_tooling = package / "repository_tooling"
    repository_tooling.mkdir()
    owner = repository_tooling / "repository_understanding_policy.py"
    owner.write_text("from hashmarks.codemap import CodeMap\n", encoding="utf-8")
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    monkeypatch.setattr(contract, "HASHMARKS_INTEGRATION_ROOT", integrations)
    monkeypatch.setattr(contract, "REPOSITORY_TOOLING_ROOT", repository_tooling)

    violations = contract.check_repository_understanding_qualification_boundary_contract()

    assert any(item.path == owner and "must not import Hashmarks" in item.message for item in violations)


def test_architecture_boundary_does_not_revalidate_repository_understanding_source_ranges(monkeypatch) -> None:
    def stale_exam():
        raise ValueError("stale Repository Understanding source range")

    import oh_goon.repository_tooling.repository_understanding_material as material

    monkeypatch.setattr(material, "load_exam", stale_exam)
    assert contract.check_repository_understanding_qualification_boundary_contract() == []
