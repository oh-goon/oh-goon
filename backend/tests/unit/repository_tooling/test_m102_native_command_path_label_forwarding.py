from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)


NATIVE = "backend/src/oh_goon/repository_tooling/native_command.py"


def _write_native(root: Path, *, forwarding: str, option_dest: str = "display_aliases") -> Path:
    path = root / NATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import argparse\n"
        "import subprocess\n"
        "\n"
        "def decode_display_aliases(raw):\n"
        "    return tuple(item.rpartition('=')[::2] for item in raw)\n"
        "\n"
        "def observe_native_process(command, *, display_map=()):\n"
        "    subprocess.run(command, check=False)\n"
        "    return display_map\n"
        "\n"
        "def parser():\n"
        "    root = argparse.ArgumentParser()\n"
        f"    root.add_argument('--path-label', dest='{option_dest}', action='append', default=[])\n"
        "    root.add_argument('--owner', default='native')\n"
        "    root.add_argument('command', nargs=argparse.REMAINDER)\n"
        "    return root\n"
        "\n"
        "def main(argv=None):\n"
        "    parsed = parser().parse_args(argv)\n"
        f"{forwarding}\n",
        encoding="utf-8",
    )
    return path


def _native_messages(root: Path) -> list[str]:
    return [
        violation.message
        for violation in check_project_supply_contract(root)
        if violation.path == root / NATIVE
    ]


def test_equivalent_helper_local_dest_and_positional_spelling_is_architecture_invariant(
    tmp_path: Path,
) -> None:
    _write_native(
        tmp_path,
        forwarding=(
            "    redaction_pairs = decode_display_aliases(parsed.display_aliases)\n"
            "    return observe_native_process(parsed.command, display_map=redaction_pairs)"
        ),
    )

    assert _native_messages(tmp_path) == []


def test_direct_forwarding_without_a_named_local_is_architecture_invariant(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        option_dest="privacy_projection",
        forwarding=(
            "    return observe_native_process(\n"
            "        parsed.command,\n"
            "        display_map=decode_display_aliases(parsed.privacy_projection),\n"
            "    )"
        ),
    )

    assert _native_messages(tmp_path) == []


def test_dropped_path_label_identity_fails_closed(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        forwarding="    return observe_native_process(parsed.command)",
    )

    assert any(
        "forward parsed path-label identity" in message for message in _native_messages(tmp_path)
    )


def test_independently_reconstructed_path_labels_fail_closed(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        forwarding=(
            "    redaction_pairs = (('/tmp/private', '<tmp>'),)\n"
            "    return observe_native_process(parsed.command, display_map=redaction_pairs)"
        ),
    )

    assert any(
        "forward parsed path-label identity" in message for message in _native_messages(tmp_path)
    )


def test_substituted_non_label_input_fails_closed(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        forwarding=(
            "    redaction_pairs = decode_display_aliases([parsed.owner])\n"
            "    return observe_native_process(parsed.command, display_map=redaction_pairs)"
        ),
    )

    assert any(
        "forward parsed path-label identity" in message for message in _native_messages(tmp_path)
    )


def test_path_label_redecision_after_valid_projection_fails_closed(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        forwarding=(
            "    redaction_pairs = decode_display_aliases(parsed.display_aliases)\n"
            "    redaction_pairs = (('/tmp/private', '<tmp>'),)\n"
            "    return observe_native_process(parsed.command, display_map=redaction_pairs)"
        ),
    )

    assert any(
        "forward parsed path-label identity" in message for message in _native_messages(tmp_path)
    )


def test_missing_public_path_label_protocol_fails_closed(tmp_path: Path) -> None:
    _write_native(
        tmp_path,
        option_dest="privacy_projection",
        forwarding=(
            "    return observe_native_process(\n"
            "        parsed.command, display_map=decode_display_aliases(parsed.privacy_projection)\n"
            "    )"
        ),
    )
    path = tmp_path / NATIVE
    path.write_text(
        path.read_text(encoding="utf-8").replace("'--path-label'", "'--redact-path'"),
        encoding="utf-8",
    )

    assert any("--path-label privacy protocol" in message for message in _native_messages(tmp_path))


def test_architecture_does_not_authorize_private_forwarding_spelling() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (
        root / "backend/src/oh_goon/repository_tooling/architecture/_structural_project_supply_contract.py"
    ).read_text(encoding="utf-8")

    for private_spelling in ("path_labels=path_labels", "_path_labels(", "run_native_command"):
        assert private_spelling not in source
