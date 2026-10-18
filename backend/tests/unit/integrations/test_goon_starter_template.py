from pathlib import Path

import yaml

from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
from oh_goon.runtime.goon_templates import GoonTemplateStore

ROOT = Path(__file__).resolve().parents[4]
STARTER = ROOT / "templates" / "goons" / "python" / "quality" / "goon.yml"


def test_python_quality_goon_starter_is_small_composition() -> None:
    source = GoonTemplateStore().source("python/quality")
    payload = source.content
    authored = yaml.safe_load(payload)

    assert "name" not in authored
    assert "description" not in authored
    assert source.compiled.name == "quality"
    assert source.compiled.description == ""
    assert "requirements:" not in payload
    assert "cache:" not in payload
    assert "timeout_seconds:" not in payload
    assert "python -c" not in payload
    assert all(isinstance(step, CompiledGoonImportStep) for step in source.compiled.steps)
    assert [step.goon for step in source.compiled.steps] == [
        "python/ruff",
        "python/typecheck",
        "python/test",
    ]
