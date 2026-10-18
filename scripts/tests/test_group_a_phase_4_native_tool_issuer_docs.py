"""Architectural guard: native tool issuer must not become a second resolver."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUER = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_native_tool_issuer.py"


def test_native_tools_reuse_existing_authorities_without_installing_or_launching():
    code = ISSUER.read_text()
    for phrase in (
        "_read_original_admission",
        "admitted_active_plan",
        "required_runtime_tool_names",
        "authority.tool(name)",
        "content_snapshot.snapshot_identity",
        "SIGNATURE_DOMAIN",
        "changed during issuance",
    ):
        assert phrase in code
    assert "subprocess" not in code
    assert "shutil.which" not in code
    assert "os.environ" not in code
    assert "capture_host_tools(" not in code


def test_no_live_execution_imports_issuer_module():
    for folder in (
        ROOT / "backend/src/oh_goon/runtime",
        ROOT / "backend/src/oh_goon/integrations",
    ):
        for path in folder.rglob("*.py"):
            assert "enforcer_lace_native_tool_issuer" not in path.read_text()
