from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_dependency_contract import (
    _check_native_authority_deduplication,
)


def _project_capability(root: Path, source: str) -> Path:
    path = (
        root / "backend/src/oh_goon/repository_tooling/project_dependency_capability.py"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _messages(root: Path) -> tuple[str, ...]:
    return tuple(item.message for item in _check_native_authority_deduplication(root))


def test_passive_projection_is_representation_invariant(tmp_path: Path) -> None:
    _project_capability(
        tmp_path,
        "import sys\n"
        "import tomllib\n"
        "def declared_runtime(path):\n"
        "    data = tomllib.loads(path.read_text())\n"
        "    return data['project']['requires-python']\n"
        "def observed_runtime():\n"
        "    return '.'.join(str(part) for part in sys.version_info[:3])\n"
        "def build(path):\n"
        "    declared = declared_runtime(path)\n"
        "    observed = observed_runtime()\n"
        "    return {\n"
        "        'compatibility_decision': 'native-uv-only',\n"
        "        'declared_requires_python': declared,\n"
        "        'python_version': observed,\n"
        "    }\n",
    )
    assert _messages(tmp_path) == ()


def test_old_private_helper_spellings_are_non_authoritative_decoys(
    tmp_path: Path,
) -> None:
    _project_capability(
        tmp_path,
        "# _package_capability _python_supported _minimum( are historical names only\n"
        "OLD_MARKERS = '_package_capability _python_supported _minimum('\n"
        "def build():\n"
        "    return {'compatibility_decision': 'native-uv-only'}\n",
    )
    assert _messages(tmp_path) == ()


def test_renamed_python_compatibility_redecision_is_rejected(tmp_path: Path) -> None:
    _project_capability(
        tmp_path,
        "import sys\n"
        "import tomllib\n"
        "def declared_runtime(path):\n"
        "    data = tomllib.loads(path.read_text())\n"
        "    return data['project']['requires-python']\n"
        "def accepts_runtime(spec):\n"
        "    minimum = tuple(int(part) for part in spec.split(',', 1)[0].removeprefix('>=').split('.'))\n"
        "    observed = tuple(sys.version_info[:len(minimum)])\n"
        "    return observed >= minimum\n"
        "def build(path):\n"
        "    declared = declared_runtime(path)\n"
        "    return {\n"
        "        'compatibility_decision': 'native-uv-only',\n"
        "        'python_status': 'compatible' if accepts_runtime(declared) else 'incompatible',\n"
        "    }\n",
    )
    assert any(
        "must not independently decide project compatibility" in item
        for item in _messages(tmp_path)
    )


def test_helper_extracted_python_redecision_is_rejected(tmp_path: Path) -> None:
    _project_capability(
        tmp_path,
        "import sys\n"
        "def project_floor(payload):\n"
        "    return payload['project'].get('requires-python')\n"
        "def normalized_floor(spec):\n"
        "    return tuple(int(piece) for piece in spec.removeprefix('>=').split('.'))\n"
        "def runtime_tuple():\n"
        "    return tuple(sys.version_info[:2])\n"
        "def verdict(payload):\n"
        "    floor = normalized_floor(project_floor(payload))\n"
        "    return runtime_tuple() >= floor\n"
        "def build(payload):\n"
        "    return {\n"
        "        'compatibility_decision': 'native-uv-only',\n"
        "        'python_ok': verdict(payload),\n"
        "    }\n",
    )
    assert any(
        "must not independently decide project compatibility" in item
        for item in _messages(tmp_path)
    )


def test_external_specifier_membership_redecision_is_rejected(tmp_path: Path) -> None:
    _project_capability(
        tmp_path,
        "import platform\n"
        "from packaging.specifiers import SpecifierSet as Range\n"
        "def build(payload):\n"
        "    declared = payload['project']['requires-python']\n"
        "    observed = platform.python_version()\n"
        "    accepted = Range(declared).contains(observed)\n"
        "    return {\n"
        "        'compatibility_decision': 'native-uv-only',\n"
        "        'python_status': 'compatible' if accepted else 'incompatible',\n"
        "    }\n",
    )
    assert any(
        "must not independently decide project compatibility" in item
        for item in _messages(tmp_path)
    )


def test_package_version_redecision_is_rejected_but_observation_is_allowed(
    tmp_path: Path,
) -> None:
    _project_capability(
        tmp_path,
        "import importlib.metadata\n"
        "def build(payload):\n"
        "    requirement = payload['project']['dependencies'][0]\n"
        "    observed = importlib.metadata.version('example')\n"
        "    locally_accepted = observed >= requirement\n"
        "    return {\n"
        "        'compatibility_decision': 'native-uv-only',\n"
        "        'observed_version': observed,\n"
        "        'local_compatibility': locally_accepted,\n"
        "    }\n",
    )
    assert any(
        "must not independently decide project compatibility" in item
        for item in _messages(tmp_path)
    )
