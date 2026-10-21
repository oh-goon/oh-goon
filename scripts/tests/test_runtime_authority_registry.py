from __future__ import annotations

import json
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.structural_launch_contract import (
    _check_runtime_authority_registry,
)

ROOT = Path(__file__).resolve().parents[2]
OWNER = "backend/src/oh_goon/runtime/fixture_owner.py"


def _fixture(root: Path, source: str, *, classified: bool = False) -> Path:
    path = root / OWNER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    registry = root / "docs/development/RUNTIME_AUTHORITY_REGISTRY.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    owners = {OWNER: "fixture-process-lifecycle"} if classified else {}
    # A separate, valid owner lets the checker exercise unclassified additions.
    sentinel = "backend/src/oh_goon/runtime/registered_owner.py"
    (root / sentinel).write_text("import os\nos.killpg(1, 0)\n", encoding="utf-8")
    owners[sentinel] = "fixture-process-observer"
    registry.write_text(json.dumps({"owners": owners}), encoding="utf-8")
    return registry


def test_runtime_authority_registry_classifies_every_physical_owner() -> None:
    assert _check_runtime_authority_registry(ROOT) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.Popen(['command'])\n",
        "import subprocess as child\nchild.Popen(['command'])\n",
        "from subprocess import Popen\nPopen(['command'])\n",
        "from subprocess import Popen as launch\nlaunch(['command'])\n",
        "import subprocess\nlaunch = subprocess.Popen\nlaunch(['command'])\n",
        "import subprocess\nfirst = subprocess.Popen\nlaunch = first\nlaunch(['command'])\n",
        "import subprocess\ngetattr(subprocess, 'Popen')(['command'])\n",
        "import subprocess\ndef launch(popen=subprocess.Popen):\n    return popen(['command'])\n",
        "import subprocess\ndef launch(*, popen=subprocess.Popen):\n    return popen(['command'])\n",
        "def launch():\n    from subprocess import Popen as child\n    return child(['command'])\n",
        "import os as process\nprocess.killpg(1, 0)\n",
        "from os import killpg as signal_group\nsignal_group(1, 0)\n",
        "import uvicorn as server\nserver.Server(config)\n",
        "from uvicorn import Server as native_server\nnative_server(config)\n",
        "from uvicorn.server import Server\nServer(config)\n",
        "def start():\n    import uvicorn\n    return serve(uvicorn)\ndef serve(native):\n    return native.Server(config)\n",
        "import subprocess\nlaunch = lambda: subprocess.Popen(['command'])\n",
        "import subprocess\ndef launch(child=subprocess.Popen(['command'])):\n    return child\n",
        "import subprocess\nlabels = [subprocess for subprocess in ['command']]\nsubprocess.Popen(labels)\n",
        "import subprocess\nchildren = [subprocess.Popen(argv) for argv in commands]\n",
        "import subprocess\nchildren = {subprocess.Popen(argv) for argv in commands}\n",
        "import subprocess\nchildren = {key: subprocess.Popen(argv) for key, argv in commands}\n",
        "import subprocess\nchildren = (subprocess.Popen(argv) for argv in commands)\n",
        "import subprocess\nchildren = [subprocess for subprocess in subprocess.Popen(['command'])]\n",
    ],
)
def test_unclassified_imported_lifecycle_call_is_rejected(
    tmp_path: Path, source: str
) -> None:
    _fixture(tmp_path, source)

    violations = _check_runtime_authority_registry(tmp_path)

    assert [(item.path, item.message) for item in violations] == [
        (tmp_path / OWNER, "physical process lifecycle authority is unclassified")
    ]


@pytest.mark.parametrize(
    "source",
    [
        "# subprocess.Popen(['command'])\ntext = 'os.killpg(1, 0)'\n",
        "import subprocess\nsubprocess.run(['command'], check=True)\n",
        "import subprocess\nchild_type = subprocess.Popen\n",
        "import subprocess\ndef launch(child: subprocess.Popen):\n    return child\n",
        "import subprocess\ndef launch(child=subprocess.Popen):\n    return child\n",
        "class subprocess:\n    def Popen(self, argv): return argv\nsubprocess().Popen(['command'])\n",
        "class subprocess:\n    @staticmethod\n    def Popen(argv): return argv\nsubprocess.Popen(['command'])\n",
        "import subprocess\ndef launch(subprocess):\n    return subprocess.Popen(['command'])\n",
        "import subprocess\ndef launch():\n    from . import subprocess\n    return subprocess.Popen(['command'])\n",
        "import subprocess\ndef launch(child):\n    subprocess = child\n    return subprocess.Popen(['command'])\n",
        "import subprocess as child\ndef launch(child):\n    return child.Popen(['command'])\n",
        "import subprocess\nfor subprocess in candidates:\n    subprocess.Popen(['command'])\n",
        "import subprocess\ndef launch(getattr):\n    return getattr(subprocess, 'Popen')(['command'])\n",
        "def first():\n    import subprocess as child\ndef second(child):\n    return child.Popen(['command'])\n",
        "def launch(child):\n    return child.Popen(['command'])\n",
        "import subprocess\nlaunch = lambda subprocess: subprocess.Popen(['command'])\n",
        "def start(serve):\n    import uvicorn\n    return serve(uvicorn)\ndef serve(native):\n    return native.Server(config)\n",
        "from .subprocess import Popen\nPopen(['command'])\n",
        "def start():\n    import uvicorn\n    return serve(uvicorn)\n"
        "def serve(native):\n    return None\n"
        "def unrelated():\n    def serve(native):\n        return native.Server(config)\n    return serve(local)\n",
        "import subprocess\nchildren = [subprocess.Popen(argv) for subprocess in local]\n",
        "import subprocess\nchildren = {subprocess.Popen(argv) for subprocess in local}\n",
        "import subprocess\nchildren = {key: subprocess.Popen(argv) for key, subprocess in local}\n",
        "import subprocess\nchildren = (subprocess.Popen(argv) for subprocess in local)\n",
    ],
)
def test_observations_references_and_unrelated_bindings_are_not_process_owners(
    tmp_path: Path, source: str
) -> None:
    _fixture(tmp_path, source)

    assert _check_runtime_authority_registry(tmp_path) == []


@pytest.mark.parametrize(
    "binding",
    [
        "def launch(argv):\n    return argv\nlaunch(['command'])\n",
        "class launch:\n    def __init__(self, argv): pass\nlaunch(['command'])\n",
        "for launch in local:\n    launch(['command'])\n",
        "try:\n    observe()\nexcept Exception as launch:\n    launch(['command'])\n",
        "import local as launch\nlaunch(['command'])\n",
        "from local import start as launch\nlaunch(['command'])\n",
        "with manager as launch:\n    launch(['command'])\n",
        "launch, other = local\nlaunch(['command'])\n",
        "[launch, *other] = local\nlaunch(['command'])\n",
    ],
    ids=[
        "function",
        "class",
        "loop",
        "exception",
        "import",
        "from-import",
        "with",
        "tuple",
        "list",
    ],
)
def test_conflicting_bindings_do_not_resurrect_imported_callable_aliases(
    tmp_path: Path, binding: str
) -> None:
    _fixture(tmp_path, "import subprocess\nlaunch = subprocess.Popen\n" + binding)

    assert _check_runtime_authority_registry(tmp_path) == []


@pytest.mark.parametrize(
    ("source", "is_owner"),
    [
        (
            "import subprocess\nclass Worker:\n    subprocess = local\n"
            "    def launch(self):\n        return subprocess.Popen(['command'])\n",
            True,
        ),
        (
            "class Worker:\n    import subprocess\n"
            "    def launch(self):\n        return subprocess.Popen(['command'])\n",
            False,
        ),
        (
            "class Worker:\n    from subprocess import Popen as start\n"
            "    def launch(self, child=start):\n        return child(['command'])\n",
            True,
        ),
        (
            "def outer():\n    import subprocess\n    class Worker:\n"
            "        subprocess = local\n"
            "        def launch(self):\n            return subprocess.Popen(['command'])\n",
            True,
        ),
        (
            "import subprocess\ndef outer(subprocess):\n    class Worker:\n"
            "        import subprocess\n"
            "        def launch(self):\n            return subprocess.Popen(['command'])\n",
            False,
        ),
        (
            "import subprocess\nclass Parent:\n    subprocess = local\n"
            "    class Worker:\n"
            "        def launch(self):\n            return subprocess.Popen(['command'])\n",
            True,
        ),
        (
            "class Parent:\n    import subprocess\n    class Worker:\n"
            "        def launch(self):\n            return subprocess.Popen(['command'])\n",
            False,
        ),
        (
            "class Parent:\n    import subprocess\n    class Worker:\n"
            "        child = subprocess.Popen(['command'])\n",
            False,
        ),
        (
            "import subprocess\nclass Worker:\n    subprocess = local\n"
            "    launch = lambda: subprocess.Popen(['command'])\n",
            True,
        ),
        (
            "class Worker:\n    import subprocess\n"
            "    launch = lambda: subprocess.Popen(['command'])\n",
            False,
        ),
        (
            "import subprocess\nclass Worker:\n    subprocess = local\n"
            "    children = [subprocess.Popen(argv) for argv in commands]\n",
            True,
        ),
        (
            "class Worker:\n    import subprocess\n"
            "    children = [subprocess.Popen(argv) for argv in commands]\n",
            False,
        ),
        (
            "class Worker:\n    import subprocess\n"
            "    child = subprocess.Popen(['command'])\n",
            True,
        ),
    ],
    ids=[
        "method-module-import",
        "method-class-import",
        "method-class-default",
        "method-enclosing-import",
        "method-enclosing-parameter",
        "nested-method-module-import",
        "nested-method-class-import",
        "nested-class-body-import",
        "lambda-module-import",
        "lambda-class-import",
        "comprehension-module-import",
        "comprehension-class-import",
        "class-body-import",
    ],
)
def test_class_locals_do_not_become_nested_scope_bindings(
    tmp_path: Path, source: str, is_owner: bool
) -> None:
    _fixture(tmp_path, source)

    violations = _check_runtime_authority_registry(tmp_path)

    assert [(item.path, item.message) for item in violations] == (
        [(tmp_path / OWNER, "physical process lifecycle authority is unclassified")]
        if is_owner
        else []
    )


def test_classification_accepts_native_callable_default_without_rewriting_owner(
    tmp_path: Path,
) -> None:
    _fixture(
        tmp_path,
        "from subprocess import Popen as child\n"
        "def launch(*, start=child):\n    return start(['command'])\n",
        classified=True,
    )

    assert _check_runtime_authority_registry(tmp_path) == []


def test_registered_owner_without_lifecycle_call_is_stale(tmp_path: Path) -> None:
    _fixture(tmp_path, "def observe():\n    return None\n", classified=True)

    violations = _check_runtime_authority_registry(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == tmp_path / OWNER
    assert "entry is stale" in violations[0].message


@pytest.mark.parametrize("payload", [None, [], "registry", 1])
def test_registry_root_must_be_a_mapping(tmp_path: Path, payload: object) -> None:
    registry = _fixture(tmp_path, "")
    registry.write_text(json.dumps(payload), encoding="utf-8")

    violations = _check_runtime_authority_registry(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == registry
    assert "registry is invalid" in violations[0].message


@pytest.mark.parametrize("domain", [None, "", "  ", 1, {}])
def test_owner_requires_a_nonempty_semantic_domain(
    tmp_path: Path, domain: object
) -> None:
    registry = _fixture(tmp_path, "import subprocess\nsubprocess.Popen(['command'])\n")
    registry.write_text(json.dumps({"owners": {OWNER: domain}}), encoding="utf-8")

    violations = _check_runtime_authority_registry(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == registry
    assert "semantic domain" in violations[0].message


def test_invalid_registry_json_reports_a_violation(tmp_path: Path) -> None:
    registry = _fixture(tmp_path, "")
    registry.write_text("{", encoding="utf-8")

    violations = _check_runtime_authority_registry(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == registry
    assert "registry is invalid" in violations[0].message


@pytest.mark.parametrize(
    "owner",
    [
        "/tmp/owner.py",
        "../owner.py",
        "backend/src/oh_goon/../owner.py",
        "backend//src/oh_goon/owner.py",
        "frontend/owner.py",
        "backend/src/oh_goon/owner.txt",
    ],
)
def test_registry_paths_cannot_escape_backend_source(
    tmp_path: Path, owner: str
) -> None:
    registry = _fixture(tmp_path, "")
    registry.write_text(
        json.dumps({"owners": {owner: "fixture-lifecycle"}}), encoding="utf-8"
    )

    violations = _check_runtime_authority_registry(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == registry
    assert "canonical backend Python path" in violations[0].message


def test_architecture_entrypoint_reports_registry_violations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from oh_goon.repository_tooling import check_architecture
    from oh_goon.repository_tooling.architecture import structural_authority_contract
    from oh_goon.repository_tooling.architecture.common import Violation

    marker = Violation(
        ROOT / "docs/development/RUNTIME_AUTHORITY_REGISTRY.json", "registry marker"
    )
    monkeypatch.setattr(
        structural_authority_contract,
        "_check_runtime_authority_registry",
        lambda _root: [marker],
    )
    monkeypatch.setattr(
        check_architecture,
        "CHECKS",
        tuple(
            check
            for check in check_architecture.CHECKS
            if check
            is structural_authority_contract.check_structural_authority_contract
        ),
    )

    assert check_architecture.main() == 1
    assert "registry marker" in capsys.readouterr().err
