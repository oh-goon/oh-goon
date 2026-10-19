"""Run the canonical operator lifecycle against an exact Oh-Goon source ZIP.

This is intentionally stdlib-only so artifact extraction happens before the artifact's
own developer environment exists. The extracted source tree then owns its normal
bootstrap/certification commands directly; this helper neither resolves dependencies
nor manufactures Git metadata as a prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(root: Path, *argv: str) -> None:
    subprocess.run(argv, cwd=root, check=True)


def _run_capture(root: Path, *argv: str) -> str:
    result = subprocess.run(
        argv,
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout, end="")
    result.check_returncode()
    return result.stdout


def _run_deep_public_dogfood(root: Path) -> None:
    """Exercise self-hosting only through the installed public Oh-Goon CLI."""

    cli = root / "backend/.venv/bin/oh-goon"
    verify_output = _run_capture(root, str(cli), "self", "verify")
    run_ids = re.findall(r"^Fleet Run ID:\s*(\S+)\s*$", verify_output, flags=re.MULTILINE)
    if len(run_ids) != 1:
        raise RuntimeError(
            "deep dogfood requires exactly one public Fleet Run ID from oh-goon self verify"
        )
    _run(root, str(cli), "dev", "explain-run", run_ids[0])
    _run(root, str(cli), "self", "certify")


def _single_root(extract_dir: Path) -> Path:
    roots = [path for path in extract_dir.iterdir() if path.is_dir()]
    files = [path for path in extract_dir.iterdir() if path.is_file()]
    if files or len(roots) != 1:
        raise SystemExit("Source ZIP must extract to exactly one top-level directory")
    return roots[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Certify a fresh exact source ZIP through the real operator lifecycle"
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="stop after init/startup/admission/lifecycle tests; default continues through certification",
    )
    parser.add_argument(
        "--deep-dogfood",
        action="store_true",
        help=(
            "after the normal lifecycle gates, self-host the extracted source through the installed "
            "public Oh-Goon CLI; diagnostic dogfood only, never certification authority"
        ),
    )
    parser.add_argument(
        "--expected-sha256",
        help=(
            "required with --deep-dogfood; bind dogfood to the exact retained artifact digest "
            "reported by prepare-release"
        ),
    )
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    if not artifact.is_file():
        parser.error(f"artifact does not exist: {artifact}")
    if not zipfile.is_zipfile(artifact):
        parser.error(f"artifact is not a ZIP: {artifact}")
    if args.deep_dogfood:
        expected = (args.expected_sha256 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            parser.error(
                "--deep-dogfood requires --expected-sha256 <64-hex retained artifact digest>"
            )
        observed = _sha256(artifact)
        if observed != expected:
            parser.error(
                "deep-dogfood artifact digest does not match the retained prepare-release receipt: "
                f"expected {expected}, observed {observed}"
            )
    with tempfile.TemporaryDirectory(prefix="oh-goon-real-lifecycle-") as temp:
        extract_dir = Path(temp) / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(artifact) as archive:
            archive.extractall(extract_dir)
        root = _single_root(extract_dir)

        # Public/operator entrypoints only. No private helper is substituted for these.
        _run(root, "make", "init")
        _run(root, "make", "local-state-prelaunch-check")
        _run(root, "make", "goon-static-admission")
        _run(root, "make", "test-real-lifecycle")
        _run(root, "make", "test-real-world")
        if args.deep_dogfood:
            _run_deep_public_dogfood(root)
        if not args.bootstrap_only:
            _run(root, "make", "certify")

    print("REAL LIFECYCLE ARTIFACT CERTIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
