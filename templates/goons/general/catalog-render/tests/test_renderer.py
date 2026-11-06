from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from oh_goon_catalog_renderer import (
    CatalogCandidate,
    candidate_from_json,
    render_catalog,
)


class CatalogRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {
            "name": "order-gateway",
            "description": "Synthetic demonstration.",
            "owner": "group:default/platform-team",
            "type": "service",
            "lifecycle": "experimental",
            "languages": ["python", "typescript"],
        }

    def test_exact_reviewed_descriptor(self) -> None:
        candidate = CatalogCandidate.from_mapping(self.values)
        self.assertEqual(
            render_catalog(candidate),
            "apiVersion: backstage.io/v1alpha1\nkind: Component\nmetadata:\n"
            '  name: "order-gateway"\n  description: "Synthetic demonstration."\n'
            '  tags:\n  - "python"\n  - "typescript"\nspec:\n'
            '  type: "service"\n  lifecycle: "experimental"\n'
            '  owner: "group:default/platform-team"\n',
        )

    def test_unknown_duplicate_and_oversize_values_refuse(self) -> None:
        with self.assertRaises(ValueError):
            CatalogCandidate.from_mapping({**self.values, "unreviewed": "private"})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            candidate_from_json('{"owner":"a","owner":"b"}')
        with self.assertRaisesRegex(ValueError, "size budget"):
            candidate_from_json(b" " * 16_385)

    def test_native_environment_input_is_exact_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                **os.environ,
                "CATALOG_CANDIDATE_JSON": json.dumps(self.values),
                "UNSELECTED_SECRET": "denied-sentinel",
            }
            command = [
                sys.executable,
                "-m",
                "oh_goon_catalog_renderer.cli",
                "--environment",
            ]
            first = subprocess.run(
                command, cwd=directory, env=environment, capture_output=True, timeout=10
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            output = Path(directory) / "catalog-info.yaml"
            expected = render_catalog(
                CatalogCandidate.from_mapping(self.values)
            ).encode()
            self.assertEqual(output.read_bytes(), expected)
            second = subprocess.run(
                command, cwd=directory, env=environment, capture_output=True, timeout=10
            )
            self.assertEqual(second.returncode, 2)
            self.assertEqual(output.read_bytes(), expected)
            self.assertNotIn(
                b"denied-sentinel", expected + first.stdout + second.stderr
            )


if __name__ == "__main__":
    unittest.main()
