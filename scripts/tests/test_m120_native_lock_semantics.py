from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    _check_browser_dependency_identity,
    _check_release_stamp_and_goon_network,
)


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _browser_messages(root: Path) -> list[str]:
    return [item.message for item in _check_browser_dependency_identity(root)]


def _release_messages(root: Path, path: Path) -> list[str]:
    return [
        item.message
        for item in _check_release_stamp_and_goon_network(root)
        if item.path == path
    ]


def _release_prelude(extra: str) -> str:
    return (
        "import subprocess\n"
        "from pathlib import Path\n"
        "BACKEND_LOCK = Path('backend') / 'uv.lock'\n"
        "FRONTEND_LOCK = Path('frontend') / 'package-lock.json'\n"
        "def native(version):\n"
        "    subprocess.run(('uv', 'version', '--frozen', version))\n"
        "    subprocess.run(('npm', 'version', version))\n"
        + extra
    )


def test_browser_rejects_renamed_lock_bytes_parsed_by_json_alias(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/browser_acceptance_container.py",
        "import json as codec\n"
        "def select(root):\n"
        "    authority = root / 'frontend' / 'package-lock.json'\n"
        "    opaque = authority.read_bytes()\n"
        "    resolved = codec.loads(opaque)\n"
        "    return resolved['packages']\n",
    )

    assert any(
        "must not parse package-lock semantics in parallel with npm" in message
        for message in _browser_messages(tmp_path)
    )


def test_browser_rejects_json_load_after_helper_extraction(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/browser_acceptance_container.py",
        "from json import load as decode_document\n"
        "def decode(path):\n"
        "    with path.open() as stream:\n"
        "        return decode_document(stream)\n"
        "def select(root):\n"
        "    source = root / 'frontend/package-lock.json'\n"
        "    return decode(source)\n",
    )

    assert any(
        "must not parse package-lock semantics in parallel with npm" in message
        for message in _browser_messages(tmp_path)
    )


def test_browser_old_marker_decoys_and_npm_json_output_are_not_authority(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/browser_acceptance_container.py",
        "import json\n"
        "# lock_path.read_text payload.get(\"packages\") packages.get(package)\n"
        "def select(result):\n"
        "    payload = json.loads(result.stdout)\n"
        "    return payload.get('dependencies')\n",
    )

    assert _browser_messages(tmp_path) == []


def test_release_rejects_backend_lock_toml_alias_through_helper(tmp_path: Path) -> None:
    stamp = _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        _release_prelude(
            "import tomllib as document_parser\n"
            "def interpret(raw):\n"
            "    return document_parser.loads(raw.decode())\n"
            "def inspect():\n"
            "    exact = BACKEND_LOCK.read_bytes()\n"
            "    return interpret(exact)\n"
        ),
    )

    assert any(
        "must not manually reinterpret package-manager lock metadata" in message
        for message in _release_messages(tmp_path, stamp)
    )


def test_release_rejects_frontend_lock_json_load_after_path_helper(tmp_path: Path) -> None:
    stamp = _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        _release_prelude(
            "from json import load as decode_document\n"
            "def interpret(path):\n"
            "    with path.open('r', encoding='utf-8') as stream:\n"
            "        return decode_document(stream)\n"
            "def inspect():\n"
            "    return interpret(FRONTEND_LOCK)\n"
        ),
    )

    assert any(
        "must not manually reinterpret package-manager lock metadata" in message
        for message in _release_messages(tmp_path, stamp)
    )


def test_release_exact_lock_byte_receipts_and_decoy_markers_are_allowed(tmp_path: Path) -> None:
    stamp = _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        _release_prelude(
            "import hashlib\n"
            "# tomllib backend_lock_text package_lock = json.loads BACKEND_LOCK.read_text FRONTEND_LOCK.read_text\n"
            "def digest(path):\n"
            "    return hashlib.sha256(path.read_bytes()).hexdigest()\n"
            "def inspect():\n"
            "    before = FRONTEND_LOCK.read_bytes()\n"
            "    backend = digest(BACKEND_LOCK)\n"
            "    return before == FRONTEND_LOCK.read_bytes(), backend\n"
        ),
    )

    messages = _release_messages(tmp_path, stamp)
    assert not any(
        "must not manually reinterpret package-manager lock metadata" in message
        for message in messages
    )


def test_release_unrelated_json_parsing_does_not_taint_lock_receipts(tmp_path: Path) -> None:
    stamp = _write(
        tmp_path,
        "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        _release_prelude(
            "import json as codec\n"
            "OPENAPI = Path('generated/openapi/openapi.json')\n"
            "def inspect():\n"
            "    receipt = FRONTEND_LOCK.read_bytes()\n"
            "    document = codec.loads(OPENAPI.read_text(encoding='utf-8'))\n"
            "    return len(receipt), document.get('info')\n"
        ),
    )

    messages = _release_messages(tmp_path, stamp)
    assert not any(
        "must not manually reinterpret package-manager lock metadata" in message
        for message in messages
    )
