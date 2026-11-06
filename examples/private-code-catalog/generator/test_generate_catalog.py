"""Fast native regression for the independent catalog example."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = HERE.parent / "expected" / "catalog-info.yaml"


class CatalogExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        shutil.copy2(HERE / "generate_catalog.py", self.work / "generate_catalog.py")
        self.data = json.loads((HERE / "catalog-input.json").read_text(encoding="utf-8"))
        self._save()

    def _save(self) -> None:
        (self.work / "catalog-input.json").write_text(
            json.dumps(self.data), encoding="utf-8"
        )

    def _run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "generate_catalog.py"],
            cwd=self.work,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_output_matches_a_real_backstage_descriptor(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.work / "catalog-info.yaml").read_bytes(), EXPECTED.read_bytes()
        )
        self.assertNotIn(b"Demo Contributor", EXPECTED.read_bytes())
        self.assertNotIn(b"src/service.py", EXPECTED.read_bytes())

    def test_unapproved_field_refused_without_output(self) -> None:
        self.data["token"] = "synthetic-secret-must-never-publish"
        self._save()
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "catalog-info.yaml").exists())
        self.assertNotIn("synthetic-secret", result.stderr)

    def test_owner_requires_explicit_group_reference(self) -> None:
        self.data["owner"] = "demo@example.invalid"
        self._save()
        self.assertNotEqual(self._run().returncode, 0)
        self.assertFalse((self.work / "catalog-info.yaml").exists())

    def test_language_tags_reject_invalid_segments(self) -> None:
        self.data["languages"] = ["python--unsafe"]
        self._save()
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "catalog-info.yaml").exists())

    def test_output_will_not_overwrite_existing_file(self) -> None:
        output = self.work / "catalog-info.yaml"
        output.write_text("existing candidate", encoding="utf-8")
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "existing candidate")


if __name__ == "__main__":
    unittest.main()
