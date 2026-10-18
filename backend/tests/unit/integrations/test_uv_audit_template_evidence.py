from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
GOON = ROOT / "templates/goons/python/uv-audit/goon.yml"


def test_uv_audit_runs_tool_directly_and_uses_game_tape_for_stdout_status() -> None:
    data = yaml.safe_load(GOON.read_text(encoding="utf-8"))
    assert data["steps"] == [
        {
            "run": "uv --preview audit --frozen --no-python-downloads --output-format json",
            "capabilities": {"egress": {"mode": "host_unrestricted"}},
        }
    ]
    text = GOON.read_text(encoding="utf-8")
    assert "uv-audit-tool.json" not in text
    assert "uv-audit.json" not in text
