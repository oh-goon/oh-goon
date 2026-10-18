"""A1L: refuse incomplete native kernel smoke evidence; not deployment attestation.

Read one raw JUnit document produced by the locked process test owner. Exact
names, cardinality and outcomes are required: pytest exit 0 without executing
all three real kernel cases (e.g. from skipped markers) is never a PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED = frozenset({
    "test_real_linux_distinct_uid_no_new_privs_zero_caps_and_six_roots",
    "test_real_linux_nonprivileged_caller_rejects_privileged_sibling_thread",
    "test_real_linux_caller_drops_root_but_sibling_retains_root",
})
SCHEMA = "oh-goon.a1-kernel-custody-diagnostic/v1"


class CustodyReceiptError(ValueError):
    pass


def _regular_bytes(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CustodyReceiptError("native custody evidence must be an unlinked regular file")
    return path.read_bytes()


def verify(junit: Path, log: Path, pytest_exit: int) -> dict[str, object]:
    junit_raw = _regular_bytes(junit)
    log_raw = _regular_bytes(log)
    if not junit_raw or not log_raw:
        raise CustodyReceiptError("missing native kernel raw test proof")
    if pytest_exit != 0:
        raise CustodyReceiptError("native kernel test owner failed")
    try:
        root = ET.fromstring(junit_raw)
    except ET.ParseError as exc:
        raise CustodyReceiptError("invalid native kernel JUnit") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise CustodyReceiptError("unrecognized native kernel JUnit root")
    suites = [root] if root.tag == "testsuite" else list(root)
    if len(suites) != 1 or suites[0].tag != "testsuite":
        raise CustodyReceiptError("unexpected kernel test suite count")
    suite = suites[0]
    cases = list(suite.findall("testcase"))
    names = [case.get("name") for case in cases]
    if len(cases) != len(EXPECTED) or set(names) != EXPECTED or len(set(names)) != len(names):
        raise CustodyReceiptError("missing, extra or duplicate real-kernel test identity")
    if any((case.get("classname") or "") not in {"", "test_a1h_real_linux_process_custody"}
           for case in cases):
        raise CustodyReceiptError("native kernel test cases came from unexpected module")
    if any(list(case) or case.get("status") in {"skipped", "failed"} for case in cases):
        raise CustodyReceiptError("native kernel test skipped, failed or emitted unknown result")
    for key, value in {"tests": 3, "skipped": 0, "failures": 0, "errors": 0}.items():
        try:
            parsed = int(suite.attrib[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise CustodyReceiptError(f"missing native JUnit {key} count") from exc
        if parsed != value:
            raise CustodyReceiptError(f"contradictory native JUnit {key} count")
    return {
        "schema": SCHEMA,
        "status": "THREE_KERNEL_SMOKES_EXECUTED_DIAGNOSTIC_ONLY",
        "test_names": sorted(EXPECTED),
        "test_count": len(cases),
        "pytest_exit": 0,
        "junit_sha256": hashlib.sha256(junit_raw).hexdigest(),
        "log_sha256": hashlib.sha256(log_raw).hexdigest(),
        "native_wsl": "NOT_QUALIFIED_BY_THIS_RECEIPT",
        "real_deployment_custody": "NOT_ATTESTED",
        "app_pass": "NOT_ESTABLISHED",
        "release": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--pytest-exit", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.junit, args.log, args.pytest_exit)
    except (CustodyReceiptError, OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "REJECTED_INCOMPLETE_NATIVE_KERNEL_PROOF",
                          "reason": str(exc), "app_pass": "NOT_ESTABLISHED"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
