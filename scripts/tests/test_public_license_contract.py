from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_license_is_canonical_mit_text() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith(
        "MIT License\n\nCopyright (c) 2026 Hugo Labraaten\n\n"
    )
    assert (
        "Permission is hereby granted, free of charge, to any person obtaining a copy"
        in license_text
    )
    assert (
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR'
        in license_text
    )
    assert license_text.endswith(
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n"
    )
