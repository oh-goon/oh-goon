"""The census enumerates current sources and refuses unknown example coverage."""

from pathlib import Path

import pytest

from oh_goon.repository_tooling.template_ux_a_inventory import collect
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore


ROOT = Path(__file__).resolve().parents[2]


def test_live_census_covers_every_store_package_and_example() -> None:
    inventory = collect(ROOT)
    expected = {("goon", key) for key in GoonTemplateStore().keys()} | {
        ("plan", key) for key in PlanTemplateStore().keys()
    }
    assert {(x["kind"], x["key"]) for x in inventory["packages"]} == expected
    assert {x["name"] for x in inventory["examples"]} == {
        path.name for path in (ROOT / "examples").iterdir() if path.is_dir()
    }
    assert inventory["metrics"]["unclassified_examples"] == 0
    assert inventory["metrics"]["unresolved_readme_links"] == 0
    assert all(x["readiness"] == "not-executed" for x in inventory["packages"])
    assert inventory["source_revision"]["commit"]


def test_new_unclassified_example_is_visible_and_incomplete(tmp_path: Path) -> None:
    for kind in ("goons", "plans"):
        (tmp_path / "templates" / kind).mkdir(parents=True)
    example = tmp_path / "examples" / "new-example"
    example.mkdir(parents=True)
    (example / "README.md").write_text("# New example\n", encoding="utf-8")

    inventory = collect(tmp_path)

    assert inventory["metrics"]["example_directories"] == 1
    assert inventory["metrics"]["unclassified_examples"] == 1
    assert inventory["examples"][0]["name"] == "new-example"
    assert inventory["examples"][0]["classification_recognized"] is False
    assert inventory["source_revision"]["commit"] is None

    (example / "README.md").write_text(
        "# New example\n\nClassification: preview-only-fixture\n", encoding="utf-8"
    )
    classified = collect(tmp_path)
    assert classified["metrics"]["unclassified_examples"] == 0
    assert classified["examples"][0]["selection_present"] is False
    assert "execution not established" in classified["examples"][0]["qualification"]


def test_catalog_usage_matches_cli_inventory_and_current_package_explanations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from oh_goon.cli import main
    from oh_goon.core.template_identity import TemplateClassification, template_usage

    import json

    packages = collect(ROOT)["packages"]
    assert main(["templates", "list", "--json"]) == 0
    listed = {
        (item["kind"], item["template_key"]): item
        for item in json.loads(capsys.readouterr().out)["templates"]
    }
    assert main(["templates", "list"]) == 0
    human = capsys.readouterr().out
    for package in packages:
        expected = template_usage(TemplateClassification(package["classification"]))
        item = listed[package["kind"], package["key"]]
        assert {name: item[name] for name in expected} == expected
        assert {name: package[name] for name in expected} == expected
        line = next(
            line
            for line in human.splitlines()
            if f"  {package['key']} —" in line and line.startswith(package["kind"])
        )
        assert expected["usage"] in line
        assert f"readiness={expected['readiness']}" in line
        assert f"prerequisites={expected['prerequisites']}" in line
        explanation = (ROOT / package["readme_path"]).read_text()
        assert f"**Status:** {expected['usage'].replace('-', ' ')}." in explanation
