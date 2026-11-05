from pathlib import Path

import pytest

from oh_goon.repository_tooling import goon_static_admission as command
from oh_goon.services.goon_static_admission import CanonicalGoonAdmissionPayload


@pytest.mark.parametrize("unresolved", [0, 1])
def test_permission_admission_does_not_claim_native_execution_closure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unresolved: int
) -> None:
    payload: CanonicalGoonAdmissionPayload = {
        "schema": command.SCHEMA,
        "status": "PASS",
        "goon_count": 1,
        "step_count": 1,
        "requirements_identity": None,
        "execution_authority_identity": None,
        "authority_closure_count": 1,
        "authority_closures_proven": 0 if unresolved else 1,
        "unresolved_execution_edge_count": unresolved,
        "package_executable_authority_count": 0,
        "issues": [],
    }

    def compile_source(_root: Path) -> CanonicalGoonAdmissionPayload:
        return payload

    monkeypatch.setattr(command, "compile_canonical_goons", compile_source)

    assert command.main([]) == 0
    output = capsys.readouterr().out
    assert "within declared tool permissions" in output
    assert "closed declared execution authority" not in output
    assert "proven" not in output
    assert ("Source execution edges remain unresolved." in output) is bool(unresolved)
