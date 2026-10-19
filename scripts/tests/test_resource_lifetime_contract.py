from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "backend" / "src" / "oh_goon"

# These forms materialize a complete file even though the operation only needs a
# streaming digest, prefix, encoded transport, or archive copy.  Payload-semantic
# reads remain legal and are reviewed by their owner.
FORBIDDEN = {
    "digest from whole-file read": re.compile(r"hashlib\.sha256\([^\n]*read_bytes\(\)"),
    "digest update from whole-file read": re.compile(r"\.update\([^\n]*read_bytes\(\)"),
    "base64 from whole-file read": re.compile(
        r"base64\.b64encode\([^\n]*read_bytes\(\)"
    ),
    "prefix after whole-file read": re.compile(r"read_bytes\(\)\s*\[:"),
    "zip write from whole-file read": re.compile(r"writestr\([^\n]*read_bytes\(\)"),
}


def test_product_does_not_materialize_whole_files_for_stream_only_work() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for label, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{relative}:{line}: {label}")
    assert not violations, "\n".join(violations)
