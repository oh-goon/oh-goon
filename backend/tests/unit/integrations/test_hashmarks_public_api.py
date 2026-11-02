"""The public evidence boundary preserves lazy loading and implementation identity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.integrations import hashmarks as public_hashmarks
from oh_goon.integrations.hashmarks import __main__ as hashmarks_entrypoint


def test_public_package_and_module_entrypoint_share_one_supervised_tool() -> None:
    assert hashmarks_entrypoint.agent_evidence_main is public_hashmarks.agent_evidence_main
    assert callable(public_hashmarks.require_loaded_hashmarks_runtime)
    assert "agent_evidence_main" in public_hashmarks.__all__


def test_removed_legacy_translation_paths_stay_absent() -> None:
    root = Path(__file__).resolve().parents[4]
    assert not (
        root / "backend/src/oh_goon/integrations/hashmarks/work_translation.py"
    ).exists()
    assert not (
        root / "backend/tests/unit/integrations/test_hashmarks_work_translation.py"
    ).exists()


@pytest.mark.process
def test_public_api_exports_original_objects_without_loading_the_producer() -> None:
    script = """
import importlib
import importlib.abc
import sys

class NoProducer(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'hashmarks' or fullname.startswith('hashmarks.'):
            raise AssertionError('public imports must not load the producer')

sys.meta_path.insert(0, NoProducer())
import oh_goon.integrations.hashmarks as evidence
prefix = evidence.__name__ + '.'
assert evidence.__all__
assert not any(name.startswith(prefix) for name in sys.modules)
runtime = evidence.require_loaded_hashmarks_runtime
assert runtime.__module__ == prefix + 'runtime_contract'
assert set(name for name in sys.modules if name.startswith(prefix)) == {
    prefix + 'runtime_contract'
}
try:
    evidence.unreviewed_capability
except AttributeError:
    pass
else:
    raise AssertionError('unknown capability was accepted')
for name in evidence.__all__:
    exported = getattr(evidence, name)
    assert callable(exported), name
    owner = importlib.import_module(exported.__module__)
    assert exported is getattr(owner, exported.__name__), name
assert not any(name == 'hashmarks' or name.startswith('hashmarks.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
