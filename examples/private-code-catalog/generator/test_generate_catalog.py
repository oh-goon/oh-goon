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
        self.data = json.loads(
            (HERE / "catalog-input.json").read_text(encoding="utf-8")
        )
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

    def test_input_at_size_budget_is_accepted(self) -> None:
        source = self.work / "catalog-input.json"
        raw = source.read_bytes()
        source.write_bytes(raw + b" " * (16_384 - len(raw)))
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.work / "catalog-info.yaml").read_bytes(), EXPECTED.read_bytes()
        )

    def test_input_over_size_budget_is_refused_without_output(self) -> None:
        source = self.work / "catalog-input.json"
        raw = source.read_bytes()
        source.write_bytes(raw + b" " * (16_385 - len(raw)))
        result = self._run()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("approved metadata exceeds size budget", result.stderr)
        self.assertFalse((self.work / "catalog-info.yaml").exists())

    @unittest.skipUnless(sys.platform == "linux", "requires native RLIMIT_AS")
    def test_large_input_is_refused_with_bounded_process_memory(self) -> None:
        # A sparse input costs no large allocation in this test. The child must
        # refuse it before reading more bytes than its process budget permits.
        with (self.work / "catalog-input.json").open("wb") as source:
            source.truncate(64 * 1024 * 1024)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import resource, runpy; "
                "resource.setrlimit(resource.RLIMIT_AS, (67108864, 67108864)); "
                "runpy.run_path('generate_catalog.py', run_name='__main__')",
            ],
            cwd=self.work,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("approved metadata exceeds size budget", result.stderr)
        self.assertFalse((self.work / "catalog-info.yaml").exists())


if __name__ == "__main__":
    unittest.main()
