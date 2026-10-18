from tests.factories.runtime_authority import authorized_command_result


def test_authorized_command_result_always_carries_exact_execution_materialization() -> None:
    result = authorized_command_result(("fake",), stdout="ok")

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.runtime_authority is not None
    assert result.runtime_authority.execution_materialization_identity is not None
