"""Catalog discovery and caller overrides use existing owners without runtime startup."""

import json

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore


def test_catalog_and_source_are_exact_and_do_not_start_runtime(monkeypatch, capsys):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("discovery must not create runtime"),
    )
    assert main(["templates", "list", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["templates"]
    assert {(item["kind"], item["template_key"]) for item in entries} == {
        *(("plan", key) for key in PlanTemplateStore().keys()),
        *(("goon", key) for key in GoonTemplateStore().keys()),
    }
    for kind, store in (("plan", PlanTemplateStore()), ("goon", GoonTemplateStore())):
        assert main(["templates", "show", "python/quality", "--kind", kind]) == 0
        assert capsys.readouterr().out == store.source("python/quality").content
    assert main(["templates", "show", "python/not-present", "--kind", "plan"]) == 2
    assert not capsys.readouterr().out
