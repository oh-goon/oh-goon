from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
GOON = ROOT / "templates/goons/security/trivy-audit/goon.yml"


def test_trivy_audit_runs_tool_directly_and_keeps_only_real_report_artifact() -> None:
    data = yaml.safe_load(GOON.read_text(encoding="utf-8"))
    assert data["steps"] == [
        {
            "run": "trivy fs --disable-telemetry --skip-version-check --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --no-progress --format json --output trivy-audit.json .",
            "artifacts": [{"path": "trivy-audit.json", "retention": "content_store"}],
            "capabilities": {"egress": {"mode": "host_unrestricted"}},
        }
    ]
    assert "trivy-audit-tool.json" not in GOON.read_text(encoding="utf-8")
