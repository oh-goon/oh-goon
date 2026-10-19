"""M172 contract owner and admission status must be explicit in shipped docs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compiler_does_not_accept_caller_claimed_derived_identities() -> None:
    code = (
        ROOT / "backend/src/oh_goon/services/fleet_native_run_contract_compiler.py"
    ).read_text()
    assert "DERIVED_FIELDS = AUTHORITY_FIELDS" in code
    assert "DEPLOYMENT_POLICY_FIELDS = CONTRACT_KEYS - DERIVED_FIELDS" in code
    assert "prework.read(journal=prework.journal" in code
    assert "check_uv=True" in code and "check_uv=False" in code
    assert "AdmittedRunContract.freeze({**deployment, **first})" in code
    assert "journal.admit_run_in(" not in code
