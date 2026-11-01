"""The public evidence boundary preserves lazy loading and implementation identity."""

from __future__ import annotations

import subprocess
import sys

import pytest


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
