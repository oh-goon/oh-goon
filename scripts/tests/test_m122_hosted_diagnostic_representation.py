from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_authority_execution import (
    _check_hosted_diagnostic_contract,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backend_core_test_diagnostic.sh"
SELECTOR = ROOT / "scripts/host_python.sh"
CAPABILITY = (
    ROOT / "backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py"
)


def _fixture(
    tmp_path: Path,
    text: str,
    *,
    capability_name: str = "hosted_dependency_capability.py",
) -> Path:
    script = tmp_path / "scripts/backend_core_test_diagnostic.sh"
    script.parent.mkdir(parents=True)
    script.write_text(text, encoding="utf-8")
    (script.parent / "host_python.sh").write_bytes(SELECTOR.read_bytes())
    capability = tmp_path / "backend/src/oh_goon/repository_tooling" / capability_name
    capability.parent.mkdir(parents=True)
    capability.write_bytes(CAPABILITY.read_bytes())
    return script


def _messages(script: Path) -> list[str]:
    return [item.message for item in _check_hosted_diagnostic_contract(script)]


def test_split_assignment_and_export_is_representation_only(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8").replace(
        'export PYTHONPATH="$ROOT/backend/src"',
        'SOURCE_ROOT="$ROOT/backend/src"\nPYTHONPATH="$SOURCE_ROOT"\nexport PYTHONPATH',
    )
    assert _messages(_fixture(tmp_path, text)) == []


def test_command_arrays_and_aliases_are_representation_only(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    text = text.replace(
        'for surface in "${surfaces[@]}"; do',
        'HOST_EXEC="$PYTHON_BIN"\nBATCH_MODULE=oh_goon.repository_tooling.test_batches\nBATCH_COMMAND=("$HOST_EXEC" -P -m "$BATCH_MODULE")\n\nfor surface in "${surfaces[@]}"; do',
    ).replace(
        '    "$PYTHON_BIN" -P -m oh_goon.repository_tooling.test_batches "$path" \\\n',
        '    "${BATCH_COMMAND[@]}" "$path" \\\n',
    )
    assert _messages(_fixture(tmp_path, text)) == []


def test_private_capability_script_name_and_helper_extraction_are_representation_only(
    tmp_path: Path,
) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    text = text.replace(
        'PYTHONNOUSERSITE=1 \\\n  "$PYTHON_BIN" -I "$ROOT/backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py" \\\n  --output "$CAPABILITY_PATH"',
        'CAPABILITY_SCRIPT="$ROOT/backend/src/oh_goon/repository_tooling/host_capability_probe.py"\n'
        "classify_host() {\n"
        '  local runner=("$PYTHON_BIN" -I "$CAPABILITY_SCRIPT")\n'
        '  PYTHONNOUSERSITE=1 "${runner[@]}" --output "$CAPABILITY_PATH"\n'
        "}\n"
        "classify_host",
    )
    assert (
        _messages(_fixture(tmp_path, text, capability_name="host_capability_probe.py"))
        == []
    )


def test_capability_classification_must_precede_workload(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    block = (
        "PYTHONNOUSERSITE=1 \\\n"
        '  "$PYTHON_BIN" -I "$ROOT/backend/src/oh_goon/repository_tooling/hosted_dependency_capability.py" \\\n'
        '  --output "$CAPABILITY_PATH"\n'
    )
    assert block in text
    text = text.replace(block, "")
    text = text.replace(
        'if [[ -n "$RECEIPT_PATH" ]]; then\n  "$PYTHON_BIN" -P -m oh_goon.repository_tooling.hosted_diagnostic_receipt',
        block
        + '\nif [[ -n "$RECEIPT_PATH" ]]; then\n  "$PYTHON_BIN" -P -m oh_goon.repository_tooling.hosted_diagnostic_receipt',
    )
    messages = _messages(_fixture(tmp_path, text))
    assert any("before hosted workload" in message for message in messages)


def test_workers_must_remain_bounded(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8").replace("--workers 1", "--workers 9")
    messages = _messages(_fixture(tmp_path, text))
    assert any("worker bound" in message for message in messages)


def test_batch_timeout_must_remain_bounded(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8").replace(
        "--batch-timeout 60", "--batch-timeout 600"
    )
    messages = _messages(_fixture(tmp_path, text))
    assert any("timeout bound" in message for message in messages)


def test_dead_surface_comment_cannot_replace_real_surface(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8").replace(
        "backend-integration) printf '%s\\t%s\\n' backend/tests/integration 6 ;;",
        "backend-integration) printf '%s\\t%s\\n' backend/tests/unit 6 ;; # backend/tests/integration",
    )
    messages = _messages(_fixture(tmp_path, text))
    assert any(
        "backend-integration" in message and "surface" in message
        for message in messages
    )


def test_dead_marker_comment_cannot_restore_missing_exclusion(tmp_path: Path) -> None:
    exact = "not certification and not slow and not process and not scale and not host_dns and not dependency_complete"
    text = SCRIPT.read_text(encoding="utf-8").replace(
        f"BASE_MARKER='{exact}'",
        f"BASE_MARKER='not certification and not slow and not process and not scale and not dependency_complete' # {exact}",
    )
    messages = _messages(_fixture(tmp_path, text))
    assert any("host_dns" in message and "exclusion" in message for message in messages)


def test_hidden_native_dependency_acquisition_is_rejected(tmp_path: Path) -> None:
    text = SCRIPT.read_text(encoding="utf-8").replace(
        'command -v "$PYTHON_BIN" >/dev/null 2>&1 || {',
        'DEP_TOOL=uv\nDEP_ACTION=sync\n"$DEP_TOOL" "$DEP_ACTION" --project backend\n\ncommand -v "$PYTHON_BIN" >/dev/null 2>&1 || {',
    )
    messages = _messages(_fixture(tmp_path, text))
    assert any("dependency acquisition" in message for message in messages)
