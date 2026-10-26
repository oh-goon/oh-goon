"""Legacy opt-in live coding baseline through the original local Run.

Native Codex owns its existing sign-in. This proof uses real inference and an
immutable independent verifier; it never inspects native credential contents or
requests forced refresh. It does not qualify native-service/private-store exclusion.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from oh_goon.cli import main as cli_main
from oh_goon.cli_ai import main as ai_main
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.integrations.agent_native_execution import (
    parse_native_agent_events,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

_BROKEN_SOURCE = """def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start < result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result
"""

_VERIFICATION = """import pathlib, runpy, sys, unittest

merge_ranges = runpy.run_path(str(pathlib.Path(sys.argv[1]) / "ranges.py"))["merge_ranges"]

class MergeRangesContract(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(merge_ranges([]), [])
        self.assertEqual(merge_ranges([(4, 4)]), [(4, 4)])

    def test_unsorted_overlapping_and_contained(self):
        self.assertEqual(merge_ranges([(6, 9), (1, 4), (3, 7), (2, 2)]), [(1, 9)])

    def test_touching_adjacent_and_separate(self):
        self.assertEqual(merge_ranges([(8, 9), (3, 5), (1, 3), (6, 6)]), [(1, 6), (8, 9)])

    def test_negative_and_duplicate_ranges(self):
        self.assertEqual(merge_ranges([(0, 0), (-3, -1), (-3, -1), (2, 4)]), [(-3, 0), (2, 4)])

    def test_input_is_unchanged(self):
        ranges = [(8, 9), (1, 3), (2, 6)]
        before = list(ranges)
        self.assertEqual(merge_ranges(ranges), [(1, 6), (8, 9)])
        self.assertEqual(ranges, before)

unittest.main(argv=["live-codex-verification"])
"""

_EVENTLOG_SOURCES: dict[str, str] = {
    "eventlog/__init__.py": "",
    "eventlog/records.py": """import json


def iter_records(lines):
    for line in lines:
        yield json.loads(line)
""",
    "eventlog/summary.py": """def summarize(records, service=None):
    totals = {}
    for record in records:
        name = record["service"]
        if service and service not in name:
            continue
        item = totals.setdefault(name, {"service": name, "count": 0, "errors": 0, "total_ms": 0})
        item["count"] += 1
        item["errors"] += int(record["status"] == "error")
        item["total_ms"] = record["duration_ms"]
    return list(totals.values())
""",
    "eventlog/__main__.py": """import argparse
import json
import sys

from .records import iter_records
from .summary import summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?")
    parser.add_argument("--service")
    args = parser.parse_args()
    if args.input:
        with open(args.input, encoding="utf-8") as stream:
            records = list(iter_records(stream))
    else:
        records = list(iter_records(sys.stdin))
    print(json.dumps(summarize(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "README.md": """# Eventlog service usage report

Repair this small standard-library package without changing its public API.

`eventlog.records.iter_records(lines)` lazily consumes an iterable of NDJSON
text lines. Skip whitespace-only lines. Each remaining line must be a JSON
object containing a nonempty string `service`, a nonnegative integer
`duration_ms` (booleans are invalid), and `status` equal to `ok` or `error`.
Yield the validated dictionaries. Invalid JSON or a malformed record raises
`ValueError` whose message starts `line N:`, with the physical one-based line
number including skipped blank lines. Do not read ahead before the next record
is requested.

`eventlog.summary.summarize(records, service=None)` consumes an iterable once,
without modifying its records. Return a list sorted alphabetically by service,
with one dictionary per service containing exactly `service`, `count`, `errors`,
and `total_ms`. Count every record, count records with status `error`, and sum
all durations. If `service` is not None, include only that exact service name;
an absent service produces an empty list. Empty input also produces an empty
list.

`python -m eventlog [--service NAME] [INPUT]` reports NDJSON from stdin when
INPUT is omitted, or from a UTF-8 file. Apply the same exact service filter and
print one JSON list followed by a newline. Success exits zero with no stderr.
Invalid records or input-file errors exit 2, write a useful diagnostic to
stderr, and write nothing to stdout. A malformed record's diagnostic retains
its physical line number. Process the records as an iterable instead of loading
the entire input into a list.
""",
}

_EVENTLOG_TASK = (
    "Repair the eventlog NDJSON service usage package according to README.md. "
    "Fix the parser's lazy validation and line-numbered errors, aggregation and exact "
    "service filtering, and the CLI's stdin/file handling and exit behavior. Keep the "
    "public APIs and use only the Python standard library. Before editing, execute the "
    "exact repository_tools.list Hashmarks command from the supplied instructions. "
    "Change only eventlog/records.py, eventlog/summary.py and eventlog/__main__.py. "
    "Independent verification runs after your coding Step."
)

_EVENTLOG_VERIFICATION = r"""import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from eventlog.records import iter_records
from eventlog.summary import summarize


def event(service="api", duration_ms=3, status="ok"):
    return {"service": service, "duration_ms": duration_ms, "status": status}


def ndjson(*records):
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def cli(*arguments, text=""):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('eventlog',run_name='__main__')",
         str(repository), *arguments],
        input=text, text=True, capture_output=True, timeout=10,
    )


class EventlogContract(unittest.TestCase):
    def test_empty_and_blank_lines(self):
        self.assertEqual(list(iter_records([])), [])
        self.assertEqual(list(iter_records(["\n", "  \t\n"])), [])
        self.assertEqual(summarize(iter(())), [])
        self.assertEqual(list(iter_records(["\n", ndjson(event(duration_ms=0)), " \n"])),
                         [event(duration_ms=0)])

    def test_mixed_service_aggregation_and_order(self):
        records = [event("zeta", 4), event("zeta", 7, "error"),
                   event("api", 3, "error"), event("api", 2)]
        before = [dict(record) for record in records]
        self.assertEqual(summarize(records), [
            {"service": "api", "count": 2, "errors": 1, "total_ms": 5},
            {"service": "zeta", "count": 2, "errors": 1, "total_ms": 11},
        ])
        self.assertEqual(records, before)

    def test_lazy_parser_and_single_pass_summary(self):
        consumed = []
        def lines():
            for index, value in enumerate([ndjson(event()), "\n", ndjson(event("worker", 8))]):
                consumed.append(index)
                yield value
        parsed = iter(iter_records(lines()))
        self.assertEqual(consumed, [])
        self.assertEqual(next(parsed), event())
        self.assertEqual(consumed, [0])
        self.assertEqual(next(parsed), event("worker", 8))
        self.assertEqual(consumed, [0, 1, 2])
        with self.assertRaises(StopIteration):
            next(parsed)
        class Once:
            def __init__(self):
                self.used = False
            def __iter__(self):
                if self.used:
                    raise AssertionError("records must be consumed once")
                self.used = True
                return iter([event(), event(duration_ms=5, status="error")])
        self.assertEqual(summarize(Once()),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 8}])

    def test_duration_validation(self):
        for value in [-1, 1.5, True, False, None, "3"]:
            with self.subTest(duration=value), self.assertRaisesRegex(ValueError, r"^line 1:"):
                list(iter_records([ndjson(event(duration_ms=value))]))

    def test_invalid_records_use_physical_line_number(self):
        for invalid in ("{broken\n", ndjson(event(duration_ms=True))):
            with self.subTest(invalid=invalid):
                values = ["\n", ndjson(event()), " \t\n", invalid]
                parsed = iter(iter_records(values))
                self.assertEqual(next(parsed), event())
                with self.assertRaisesRegex(ValueError, r"^line 4:"):
                    next(parsed)

    def test_record_shape_service_and_status_validation(self):
        invalid = [[], None, {}, event(service=""), event(service=None), event(service=7),
                   event(status="timeout"), event(status=True)]
        invalid.extend({key: value for key, value in event().items() if key != missing}
                       for missing in ("service", "duration_ms", "status"))
        for record in invalid:
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, r"^line 1:"):
                list(iter_records([ndjson(record)]))

    def test_exact_service_filter(self):
        records = [event("api-v2", 99), event("api", 7, "error"), event("api", 1)]
        self.assertEqual(summarize(iter(records), service="api"),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 8}])
        self.assertEqual(summarize(iter(records), service="missing"), [])
        self.assertEqual(summarize(iter(records), service=""), [])

    def test_cli_stdin_filter_and_errors(self):
        result = cli("--service", "api", text=ndjson(event("api-v2", 99), event("api", 7)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout),
                         [{"service": "api", "count": 1, "errors": 0, "total_ms": 7}])
        for invalid in ("{broken\n", ndjson(event(duration_ms=True))):
            with self.subTest(invalid=invalid):
                failed = cli(text=ndjson(event()) + "\n" + invalid)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, "")
                self.assertRegex(failed.stderr, r"line 3:")

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_AS streaming proof requires Linux")
    def test_cli_streams_stdin_and_file_with_bounded_memory(self):
        count = 131072
        row = ndjson(event(status="error"))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "large-events.ndjson"
            with path.open("w", encoding="utf-8") as stream:
                for _ in range(count):
                    stream.write(row)
            for mode in ("stdin", "file"):
                with self.subTest(mode=mode), path.open("rb") as stream:
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", "-c",
                         "import resource,runpy,sys;"
                         "resource.setrlimit(resource.RLIMIT_AS,(64*1024*1024,64*1024*1024));"
                         "sys.path.insert(0,sys.argv.pop(1));"
                         "runpy.run_module('eventlog',run_name='__main__')",
                         str(repository), *([str(path)] if mode == "file" else [])],
                        stdin=stream, text=True, capture_output=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assertTrue(result.stdout.endswith("\n"))
                    self.assertEqual(json.loads(result.stdout),
                                     [{"service": "api", "count": count,
                                       "errors": count, "total_ms": count * 3}])

    def test_cli_utf8_file_and_input_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "events.ndjson"
            path.write_text(ndjson(event("caf\u00e9", 2), event("caf\u00e9", 5, "error")), encoding="utf-8")
            result = cli(str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(result.stdout),
                             [{"service": "caf\u00e9", "count": 2, "errors": 1, "total_ms": 7}])
            path.write_text(ndjson(event()) + "\n{broken\n", encoding="utf-8")
            failed = cli(str(path))
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertRegex(failed.stderr, r"line 3:")
            missing = cli(str(path.with_name("missing.ndjson")))
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertTrue(missing.stderr.strip())
            path.write_bytes(b"\xff")
            invalid_encoding = cli(str(path))
            self.assertEqual(invalid_encoding.returncode, 2)
            self.assertEqual(invalid_encoding.stdout, "")
            self.assertTrue(invalid_encoding.stderr.strip())


unittest.main(argv=["live-codex-eventlog-verification"])
"""


_EVENTLOG_FEATURE_TASK = (
    "Extend the repaired eventlog package with a minimum service-count filter. "
    "Add an optional min_count=0 argument to summarize(records, service=None, min_count=0). "
    "It must be a nonnegative integer; booleans and other types are invalid, and invalid "
    "values raise ValueError. Apply the threshold to each service's final aggregate count, "
    "including services with count equal to min_count. Preserve alphabetical output, exact "
    "service matching, one-pass consumption and unchanged input records. Add CLI --min-count N "
    "with default 0 and the same nonnegative integer rule. Invalid CLI values exit 2, write "
    "a useful diagnostic to stderr and write nothing to stdout. Keep all existing parser, "
    "stdin/file and error behavior. Change only eventlog/summary.py and eventlog/__main__.py. "
    "Before editing, execute the exact repository_tools.list Hashmarks command from the "
    "supplied instructions. Independent verification runs after your coding Step."
)

_EVENTLOG_FEATURE_VERIFICATION = r"""import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from eventlog.summary import summarize


def event(service="api", duration_ms=3, status="ok"):
    return {"service": service, "duration_ms": duration_ms, "status": status}


def ndjson(*records):
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def cli(*arguments, text=""):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('eventlog',run_name='__main__')",
         str(repository), *arguments],
        input=text, text=True, capture_output=True, timeout=10,
    )


class EventlogMinimumCountContract(unittest.TestCase):
    def test_filter_uses_final_count_and_retains_order_and_totals(self):
        records = [event("zeta", 7, "error"), event("api", 2), event("rare", 99),
                   event("zeta", 3), event("api", 5, "error"), event("zeta", 4)]
        before = [dict(record) for record in records]
        self.assertEqual(summarize(records, min_count=2), [
            {"service": "api", "count": 2, "errors": 1, "total_ms": 7},
            {"service": "zeta", "count": 3, "errors": 1, "total_ms": 14},
        ])
        self.assertEqual(records, before)

    def test_threshold_edges_and_invalid_api_values(self):
        records = [event()]
        self.assertEqual(summarize(records, min_count=0),
                         [{"service": "api", "count": 1, "errors": 0, "total_ms": 3}])
        self.assertEqual(summarize(records, min_count=2), [])
        self.assertEqual(summarize(iter(()), min_count=2), [])
        for value in (-1, 1.5, True, False, None, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize(iter(()), min_count=value)

    def test_exact_service_and_single_pass(self):
        class Once:
            def __init__(self):
                self.used = False
            def __iter__(self):
                if self.used:
                    raise AssertionError("records must be consumed once")
                self.used = True
                return iter([event("api-v2", 99), event("api", 4), event("api", 6, "error")])
        self.assertEqual(summarize(Once(), service="api", min_count=2),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 10}])
        self.assertEqual(summarize(Once(), service="api-v2", min_count=2), [])
        self.assertEqual(summarize(Once(), service="missing", min_count=0), [])

    def test_cli_stdin_filter_and_invalid_arguments(self):
        text = ndjson(event("api-v2", 99), event("api", 2), event("api", 5, "error"))
        result = cli("--min-count", "2", "--service", "api", text=text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(json.loads(result.stdout),
                         [{"service": "api", "count": 2, "errors": 1, "total_ms": 7}])
        zero = cli("--min-count", "0", text=text)
        self.assertEqual(zero.returncode, 0, zero.stderr)
        self.assertEqual(zero.stderr, "")
        self.assertEqual(json.loads(zero.stdout), [
            {"service": "api", "count": 2, "errors": 1, "total_ms": 7},
            {"service": "api-v2", "count": 1, "errors": 0, "total_ms": 99},
        ])
        for value in ("-1", "1.5", "true", "invalid"):
            with self.subTest(value=value):
                failed = cli("--min-count", value, text=text)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, "")
                self.assertTrue(failed.stderr.strip())

    def test_cli_utf8_file_and_filtered_invalid_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "events.ndjson"
            path.write_text(ndjson(event("caf\u00e9", 2), event("rare", 99),
                                   event("caf\u00e9", 5, "error")), encoding="utf-8")
            result = cli("--min-count", "2", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("\n"))
            self.assertEqual(json.loads(result.stdout),
                             [{"service": "caf\u00e9", "count": 2, "errors": 1, "total_ms": 7}])
            path.write_text(ndjson(event()) + "\n{broken\n", encoding="utf-8")
            failed = cli("--min-count", "999", str(path))
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")
            self.assertRegex(failed.stderr, r"line 3:")


unittest.main(argv=["live-codex-eventlog-minimum-count-verification"])
"""


_STOCKTAKE_SOURCES = {
    "stocktake/__init__.py": "",
    "stocktake/rows.py": """import csv


def read_rows(stream):
    return [(row["sku"], int(row["quantity"])) for row in csv.DictReader(stream)]
""",
    "stocktake/reconcile.py": """def reconcile(expected, counted, only_changed=False):
    counts = dict(counted)
    return [{"sku": sku, "expected": quantity, "counted": counts.get(sku, 0),
             "delta": quantity - counts.get(sku, 0)} for sku, quantity in expected]
""",
    "stocktake/__main__.py": """import argparse
import json

from .reconcile import reconcile
from .rows import read_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("expected")
    parser.add_argument("counted")
    args = parser.parse_args()
    with open(args.expected) as expected, open(args.counted) as counted:
        print(json.dumps(reconcile(read_rows(expected), read_rows(counted))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "README.md": """# Stocktake CSV reconciliation

Repair this standard-library package while retaining its public functions.

`stocktake.rows.read_rows(stream)` reads a text stream of CSV and returns a list
of `(sku, quantity)` tuples. Require exactly the header `sku,quantity` in that
order; an empty stream, missing/repeated/extra columns, malformed CSV or rows
with the wrong number of fields raise ValueError. Ignore empty data rows. Use
standard CSV quoting, including commas and newlines inside quoted SKUs. A SKU
must contain a non-whitespace character; preserve its original spelling. A
quantity consists only of one or more ASCII decimal digits and is converted to
int. Zero and leading zeros are valid; signs, whitespace, fractions and other
numeric spellings are invalid. Reject every malformed row rather than silently
dropping it. File encoding is the caller's responsibility.

`stocktake.reconcile.reconcile(expected, counted, only_changed=False)` consumes
two iterables of already validated `(sku, quantity)` pairs once each. Sum
duplicate SKUs independently on each side and retain every SKU from either
side. Missing sides have quantity zero. Return dictionaries containing exactly
`sku`, `expected`, `counted` and `delta`, sorted by exact SKU. Delta is counted
minus expected, so shortages are negative. With only_changed=True omit exactly
the rows whose final delta is zero. Never modify either input.

`python -m stocktake [--only-changed] EXPECTED COUNTED` reads two UTF-8 CSV files
with proper CSV newline handling. Print the reconciliation as one JSON list
followed by a newline, with no stderr and exit 0. Input errors, including
malformed CSV, missing files and invalid UTF-8, exit 2 with a useful stderr
diagnostic and no stdout. Validate both complete inputs before printing.
""",
}

_STOCKTAKE_TASK = (
    "Repair the stocktake CSV reconciliation package according to README.md. "
    "Fix CSV validation, exact joining and aggregation across both inventories, "
    "shortage/surplus signs and the --only-changed CLI behavior. Preserve its public "
    "functions and use only the Python standard library. Before editing, execute the "
    "exact repository_tools.list Hashmarks command from the supplied instructions. "
    "Change only stocktake/rows.py, stocktake/reconcile.py and stocktake/__main__.py. "
    "Independent verification runs after your coding Step."
)

_STOCKTAKE_VERIFICATION = r"""import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from stocktake.rows import read_rows
from stocktake.reconcile import reconcile


def rows(text):
    return read_rows(io.StringIO(text, newline=""))


def cli(*arguments):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('stocktake',run_name='__main__')",
         str(repository), *arguments],
        text=True, capture_output=True, timeout=10,
    )


class StocktakeContract(unittest.TestCase):
    def test_header_only_zero_and_blank_data_rows(self):
        self.assertEqual(rows("sku,quantity\n"), [])
        self.assertEqual(rows("sku,quantity\n\nbolt,000\n\n"), [("bolt", 0)])
        self.assertEqual(reconcile(iter(()), iter(())), [])

    def test_csv_quoting_and_exact_unicode_sku(self):
        self.assertEqual(rows('sku,quantity\r\n"bolt,steel",2\r\n"shelf\nlabel",3\r\ncafé,4\r\n'),
                         [("bolt,steel", 2), ("shelf\nlabel", 3), ("café", 4)])
        self.assertEqual(rows("sku,quantity\n bolt ,2\n"), [(" bolt ", 2)])

    def test_rejects_invalid_headers_and_row_shapes(self):
        for text in ("", "quantity,sku\n2,bolt\n", "sku,sku\nbolt,2\n",
                     "sku,quantity,extra\nbolt,2,x\n", "sku\nbolt\n",
                     "sku,quantity\nbolt\n", "sku,quantity\nbolt,2,x\n",
                     'sku,quantity\n"unclosed,2\n', "sku,quantity\n,2\n",
                     "sku,quantity\n   ,2\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                rows(text)

    def test_rejects_invalid_quantities_after_a_valid_row(self):
        for value in ("-1", "+2", "1.5", "true", " 2", "2 ", "", "٢"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rows("sku,quantity\nvalid,1\ninvalid," + value + "\n")

    def test_full_join_duplicate_aggregation_signs_and_filter(self):
        expected = [("widget", 3), ("bolt", 2), ("widget", 4), ("even", 5)]
        counted = [("extra", 2), ("widget", 1), ("even", 2), ("widget", 4), ("even", 3)]
        all_rows = [{"sku": "bolt", "expected": 2, "counted": 0, "delta": -2},
                    {"sku": "even", "expected": 5, "counted": 5, "delta": 0},
                    {"sku": "extra", "expected": 0, "counted": 2, "delta": 2},
                    {"sku": "widget", "expected": 7, "counted": 5, "delta": -2}]
        self.assertEqual(reconcile(expected, counted), all_rows)
        self.assertEqual(reconcile(expected, counted, only_changed=True),
                         [all_rows[0], all_rows[2], all_rows[3]])
        self.assertEqual(reconcile([], [("a", 1), ("a-2", 2)]),
                         [{"sku": "a", "expected": 0, "counted": 1, "delta": 1},
                          {"sku": "a-2", "expected": 0, "counted": 2, "delta": 2}])
        self.assertEqual(reconcile([("Bolt", 2), (" bolt ", 1)], [("bolt", 3)]),
                         [{"sku": " bolt ", "expected": 1, "counted": 0, "delta": -1},
                          {"sku": "Bolt", "expected": 2, "counted": 0, "delta": -2},
                          {"sku": "bolt", "expected": 0, "counted": 3, "delta": 3}])

    def test_single_pass_without_input_mutation(self):
        class Once:
            def __init__(self, values):
                self.values, self.used = values, False
            def __iter__(self):
                if self.used:
                    raise AssertionError("inventory must be consumed once")
                self.used = True
                return iter(self.values)
        expected, counted = [["bolt", 2]], [["bolt", 3]]
        self.assertEqual(reconcile(Once(expected), Once(counted)),
                         [{"sku": "bolt", "expected": 2, "counted": 3, "delta": 1}])
        self.assertEqual(expected, [["bolt", 2]])
        self.assertEqual(counted, [["bolt", 3]])

    def test_cli_utf8_quoted_paths_filter_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected inventory.csv"
            counted = pathlib.Path(directory) / "counted inventory.csv"
            expected.write_text('sku,quantity\n"bolt,steel",2\ncafé,3\n', encoding="utf-8")
            counted.write_text('sku,quantity\ncafé,1\n"bolt,steel",2\n', encoding="utf-8")
            for arguments, values in (([], [
                {"sku": "bolt,steel", "expected": 2, "counted": 2, "delta": 0},
                {"sku": "café", "expected": 3, "counted": 1, "delta": -2}]),
                (["--only-changed"], [
                {"sku": "café", "expected": 3, "counted": 1, "delta": -2}])):
                result = cli(*arguments, str(expected), str(counted))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertTrue(result.stdout.endswith("\n"))
                self.assertEqual(json.loads(result.stdout), values)

    def test_cli_input_failures_are_atomic_for_either_input(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = pathlib.Path(directory) / "valid.csv"
            invalid = pathlib.Path(directory) / "invalid.csv"
            valid.write_text("sku,quantity\nbolt,1\n", encoding="utf-8")
            for payload in (b"sku,quantity\nbolt,1\nbad,-1\n", b"\xff",
                            b'sku,quantity\n"unclosed,2\n'):
                invalid.write_bytes(payload)
                for inputs in ((valid, invalid), (invalid, valid)):
                    with self.subTest(payload=payload, inputs=inputs):
                        result = cli("--only-changed", *(str(path) for path in inputs))
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(result.stdout, "")
                        self.assertTrue(result.stderr.strip())
            missing = cli(str(valid), str(invalid.with_name("missing.csv")))
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertTrue(missing.stderr.strip())


unittest.main(argv=["live-codex-stocktake-verification"])
"""


_DEPLOY_ORDER_SOURCES = {
    "deploy_order/__init__.py": "",
    "deploy_order/graph.py": """def order_services(dependencies):
    return sorted(dependencies)
""",
    "deploy_order/__main__.py": """import json
import sys

from .graph import order_services


def main():
    with open(sys.argv[1], encoding="utf-8") as stream:
        print(json.dumps(order_services(json.load(stream))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
    "README.md": """# Deployment ordering

Repair this standard-library deployment manifest tool.

`deploy_order.graph.order_services(dependencies)` accepts a dictionary mapping
nonempty service names to lists of dependency names. Validate the entire manifest:
keys and dependency names must be nonempty strings, values must be lists, every
dependency must be declared, and cycles (including self cycles) are invalid.
Invalid manifests raise ValueError. Duplicate dependency edges are allowed and
mean one prerequisite. Empty manifests produce []. Do not mutate any input.

Return every service exactly once, after its dependencies. At each selection,
choose the alphabetically smallest currently ready service, including services
that just became ready. Results must be independent of dictionary insertion and
dependency-list order. Use an iterative algorithm so long chains do not exhaust
the Python recursion limit.

`python -m deploy_order MANIFEST` reads a UTF-8 JSON manifest file and prints
one JSON list plus newline. Success exits 0 with no stderr. Invalid JSON,
invalid manifests and file/encoding errors exit 2 with a useful diagnostic on
stderr and nothing on stdout. Preserve the public function and CLI interface.
""",
}

_DEPLOY_ORDER_TASK = (
    "Repair the deployment manifest tool according to README.md. Implement "
    "deterministic dependency ordering, complete manifest validation and atomic CLI "
    "error handling. Use only the Python standard library. Before editing, execute "
    "the exact repository_tools.list Hashmarks command from the supplied instructions. "
    "Run that list command and the supplied repository_tools.search command in the same "
    "shell invocation, in that order, joining the two complete commands with &&. "
    "Create a private query file under the native $TMPDIR scratch directory, outside "
    "the working repository, containing exactly order_services with no newline. "
    "Replace the search template's entire <private-query-file> placeholder with its "
    "quoted actual path; do not concatenate the two commands' arguments. Both commands "
    "must succeed before editing. Inspect the frozen source evidence before editing. "
    "Change only deploy_order/graph.py and deploy_order/__main__.py. "
    "Independent verification runs after your coding Step."
)

_DEPLOY_ORDER_VERIFICATION = r"""import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from deploy_order.graph import order_services


def cli(path):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('deploy_order',run_name='__main__')",
         str(repository), str(path)],
        text=True, capture_output=True, timeout=10,
    )


class DeploymentOrderContract(unittest.TestCase):
    def test_empty_and_independent_services(self):
        self.assertEqual(order_services({}), [])
        self.assertEqual(order_services({"z": [], "a": [], "m": []}), ["a", "m", "z"])

    def test_newly_ready_services_compete_with_existing_ready_services(self):
        manifest = {"z": [], "b": ["a"], "a": [], "c": ["b"]}
        self.assertEqual(order_services(manifest), ["a", "b", "c", "z"])

    def test_shared_dependencies_duplicates_permutations_and_nonmutation(self):
        manifest = {"web": ["cache", "db", "db"], "worker": ["db"],
                    "db": [], "cache": ["db"]}
        before = copy.deepcopy(manifest)
        expected = ["db", "cache", "web", "worker"]
        self.assertEqual(order_services(manifest), expected)
        self.assertEqual(manifest, before)
        reordered = {key: list(reversed(manifest[key])) for key in reversed(manifest)}
        self.assertEqual(order_services(reordered), expected)
        self.assertEqual(order_services({"café": [], "api": ["café"]}), ["café", "api"])

    def test_invalid_shapes_and_undeclared_dependencies(self):
        for manifest in (None, [], "api", {"": []}, {1: []}, {"api": "db"},
                         {"api": ()}, {"api": None}, {"api": [1]}, {"api": [""]},
                         {"api": [[]]}, {"api": [{}]}, {"api": [True]},
                         {"api": ["missing"]}, {"ok": [], "z": ["absent"]}):
            before = copy.deepcopy(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                order_services(manifest)
            self.assertEqual(manifest, before)

    def test_cycles_including_disconnected_components(self):
        for manifest in ({"a": ["a"]}, {"a": ["b"], "b": ["a"]},
                         {"ready": [], "a": ["b"], "b": ["c"], "c": ["a"]}):
            before = copy.deepcopy(manifest)
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                order_services(manifest)
            self.assertEqual(manifest, before)

    def test_chain_beyond_recursion_limit(self):
        names = [f"service-{index:05}" for index in range(2500)]
        manifest = {name: [names[index - 1]] if index else []
                    for index, name in reversed(list(enumerate(names)))}
        self.assertEqual(order_services(manifest), names)

    def test_cli_utf8_success_and_atomic_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "deployment manifest.json"
            path.write_text(json.dumps({"api": ["café"], "café": []},
                                       ensure_ascii=False), encoding="utf-8")
            result = cli(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("\n"))
            self.assertEqual(json.loads(result.stdout), ["café", "api"])
            for payload in (b"{broken", b"[]", b'{"ok": [], "bad": ["absent"]}',
                            b'{"ok": [], "a": ["a"]}', b"\xff"):
                path.write_bytes(payload)
                result = cli(path)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())
            result = cli(path.with_name("missing.json"))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr.strip())


unittest.main(argv=["live-codex-deployment-order-verification"])
"""


_LEDGER_SOURCES = {
    "ledger/__init__.py": "",
    "ledger/records.py": """import json


def iter_events(lines):
    for line in lines:
        yield json.loads(line)
""",
    "ledger/database.py": """import sqlite3


def import_events(database, events):
    connection = sqlite3.connect(database)
    connection.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, account TEXT NOT NULL, amount_cents INTEGER NOT NULL)')
    inserted = 0
    try:
        for event in events:
            connection.execute('INSERT OR REPLACE INTO events VALUES (?, ?, ?)',
                               (event['id'], event['account'], event['amount_cents']))
            connection.commit()
            inserted += 1
        return inserted
    finally:
        connection.close()


def totals(database):
    connection = sqlite3.connect(database)
    try:
        return [{'account': row[0], 'total_cents': row[1]}
                for row in connection.execute('SELECT account, amount_cents FROM events ORDER BY account')]
    finally:
        connection.close()
""",
    "ledger/__main__.py": """import argparse
import json
import sys

from .database import import_events, totals
from .records import iter_events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('database')
    parser.add_argument('input', nargs='?')
    args = parser.parse_args()
    stream = open(args.input, encoding='utf-8') if args.input else sys.stdin
    inserted = import_events(args.database, iter_events(stream))
    print(json.dumps({'inserted': inserted, 'totals': totals(args.database)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
""",
    "README.md": """# Persistent ledger import

Repair this Python standard-library package. Native SQLite owns transactions.

`ledger.records.iter_events(lines)` lazily yields dictionaries from NDJSON text.
Skip blank lines. Each object needs a nonblank string `id`, a nonblank string
`account`, and an integer `amount_cents` in the signed SQLite 64-bit range.
Booleans are invalid amounts. Preserve exact IDs/account names, including spaces,
quotes and Unicode; do not trim them. Invalid JSON/records raise ValueError with
`line N:` at the start, using physical one-based line numbers. Do not read ahead.

`ledger.database.import_events(database, events)` consumes validated records once
and returns the number of newly inserted IDs. Use the existing `events` table
with columns `event_id`, `account`, `amount_cents`. Identical duplicates (same ID,
account and amount) are no-ops, both within a batch and across persisted imports.
A repeated ID with a different account or amount raises ValueError. An import is
one atomic transaction: conflicting duplicates, parser failures or iterator
exceptions roll back every new row in that batch while preserving prior imports.
Propagate iterator exceptions. Never overwrite
an existing event. Close owned database resources on every path.

`ledger.database.totals(database)` returns one dictionary per account, sorted by
account, with exactly `account` and `total_cents`. Sum all persisted amounts for
that account, retaining zero totals. Empty ledgers return []. No total in this
exercise exceeds SQLite's signed integer range.

`python -m ledger DATABASE [INPUT]` imports a UTF-8 NDJSON file or stdin if INPUT
is omitted. Print exactly one JSON object with `inserted` and `totals`, plus a
newline; successful imports exit 0 with no stderr. Bad input, conflicting IDs,
file/encoding errors and SQLite errors exit 2, emit a useful stderr diagnostic
and no stdout. Preserve line numbers for bad records. Failed imports must leave
previously committed events unchanged. Close owned input files and connections;
stdin belongs to the caller. Preserve public APIs; use only the standard library.
""",
}

_LEDGER_TASK = (
    "Repair the persistent SQLite ledger import package according to README.md. "
    "Fix lazy record validation, atomic imports, exact duplicate handling, aggregate "
    "totals and CLI success/error behavior. Keep the public APIs and use only the "
    "Python standard library. Before editing, execute the exact repository_tools.list "
    "Hashmarks command from the supplied instructions. Change only ledger/records.py, "
    "ledger/database.py and ledger/__main__.py. Independent verification owns its "
    "temporary databases and runs after your coding Step."
)

_LEDGER_VERIFICATION = r"""import copy
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from ledger.records import iter_events
from ledger.database import import_events, totals


def event(identifier='one', account='cash', amount=5):
    return {'id': identifier, 'account': account, 'amount_cents': amount}


def ndjson(*events):
    return ''.join(json.dumps(value, ensure_ascii=False) + '\n' for value in events)


def snapshot(database):
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            'SELECT event_id, account, amount_cents FROM events ORDER BY event_id'
        ).fetchall()


def cli(database, *arguments, text=''):
    return subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('ledger',run_name='__main__')",
         str(repository), str(database), *map(str, arguments)],
        input=text, text=True, capture_output=True, timeout=10,
    )


class LedgerContract(unittest.TestCase):
    def test_lazy_physical_line_validation(self):
        consumed = []
        def lines():
            for index, line in enumerate(['\n', ndjson(event()), ' \n', '{broken\n']):
                consumed.append(index)
                yield line
        records = iter(iter_events(lines()))
        self.assertEqual(consumed, [])
        self.assertEqual(next(records), event())
        self.assertEqual(consumed, [0, 1])
        with self.assertRaisesRegex(ValueError, '^line 4:'):
            next(records)
        self.assertEqual(list(iter_events(['\n', '  \t\n'])), [])

    def test_record_validation_and_exact_values(self):
        for invalid in ([], None, {}, event(identifier=''), event(identifier='  '),
                        event(account=''), event(account='  '),
                        event(identifier=[]), event(account=3), event(amount=True),
                        event(amount=False), event(amount=1.5), event(amount='2'),
                        event(amount=2**63), event(amount=-(2**63)-1)):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, '^line 2:'):
                list(iter_events(['\n', ndjson(invalid)]))
        values = [event(' quote\'雪 ', ' café ', -7)]
        self.assertEqual(list(iter_events([ndjson(*values)])), values)

    def test_persistent_idempotence_totals_and_single_pass_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'account ledger.sqlite'
            values = [event('a', 'cash', 8), event('b', 'cash', -8),
                      event('c', 'café', 13), event('d', 'café', -2), event('c', 'café', 13)]
            original = copy.deepcopy(values)
            class Once:
                used = False
                def __iter__(self):
                    if self.used:
                        raise AssertionError('events must be consumed once')
                    self.used = True
                    return iter(values)
            self.assertEqual(import_events(database, Once()), 4)
            self.assertEqual(values, original)
            expected = [('a', 'cash', 8), ('b', 'cash', -8),
                        ('c', 'café', 13), ('d', 'café', -2)]
            self.assertEqual(snapshot(database), expected)
            self.assertEqual(import_events(database, iter(values)), 0)
            self.assertEqual(snapshot(database), expected)
            self.assertEqual(totals(database), [
                {'account': 'café', 'total_cents': 11}, {'account': 'cash', 'total_cents': 0}])

    def test_late_conflict_rolls_back_then_corrected_retry_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'ledger.sqlite'
            seed = event('existing', 'cash', 9)
            self.assertEqual(import_events(database, iter([seed])), 1)
            before = snapshot(database)
            for conflict in (event('existing', 'cash', 10), event('existing', 'other', 9)):
                with self.subTest(conflict=conflict), self.assertRaises(ValueError):
                    import_events(database, iter([event('new', 'cash', 4), conflict]))
                self.assertEqual(snapshot(database), before)
            self.assertEqual(import_events(database, iter([event('new', 'cash', 4), seed])), 1)
            self.assertEqual(snapshot(database), [('existing', 'cash', 9), ('new', 'cash', 4)])
            before = snapshot(database)
            with self.assertRaises(ValueError):
                import_events(database, iter([event('fresh'), event('fresh', amount=6)]))
            self.assertEqual(snapshot(database), before)

    def test_parser_and_iterator_failures_are_atomic_without_intermediate_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'ledger.sqlite'
            self.assertEqual(import_events(database, iter([event('seed')])), 1)
            before = snapshot(database)
            records = iter_events([ndjson(event('pending')), '\n', '{bad\n'])
            with self.assertRaisesRegex(ValueError, '^line 3:'):
                import_events(database, records)
            self.assertEqual(snapshot(database), before)
            def interrupted():
                yield event('pending')
                self.assertEqual(snapshot(database), before)
                raise RuntimeError('input interrupted')
            with self.assertRaisesRegex(RuntimeError, 'input interrupted'):
                import_events(database, interrupted())
            self.assertEqual(snapshot(database), before)
            self.assertEqual(import_events(database, iter([event('pending')])), 1)

    def test_empty_and_signed_integer_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'ledger.sqlite'
            self.assertEqual(import_events(database, iter(())), 0)
            self.assertEqual(totals(database), [])
            values = [event('min', 'negative', -(2**63)), event('max', 'positive', 2**63-1)]
            self.assertEqual(import_events(database, iter_events(ndjson(*values).splitlines(keepends=True))), 2)
            self.assertEqual(snapshot(database), [('max', 'positive', 2**63-1),
                                                   ('min', 'negative', -(2**63))])
            self.assertEqual(totals(database), [
                {'account': 'negative', 'total_cents': -(2**63)},
                {'account': 'positive', 'total_cents': 2**63-1}])
            self.assertEqual(import_events(database, iter([
                event('large', 'exact', 2**60+3), event('adjustment', 'exact', -1)])), 2)
            self.assertEqual(totals(database), [
                {'account': 'exact', 'total_cents': 2**60+2},
                {'account': 'negative', 'total_cents': -(2**63)},
                {'account': 'positive', 'total_cents': 2**63-1}])

    def test_cli_file_stdin_persistence_and_atomic_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'café ledger.sqlite'
            source = pathlib.Path(directory) / 'quoted input.ndjson'
            values = [event("invoice'雪", 'cash', 7), event('two', 'cash', -2)]
            source.write_text(ndjson(*values), encoding='utf-8')
            result = cli(database, source)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertTrue(result.stdout.endswith('\n'))
            self.assertEqual(json.loads(result.stdout),
                             {'inserted': 2, 'totals': [{'account': 'cash', 'total_cents': 5}]})
            result = cli(database, text=ndjson(*values, event('three', 'bank', 4)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                             {'inserted': 1, 'totals': [{'account': 'bank', 'total_cents': 4},
                                                       {'account': 'cash', 'total_cents': 5}]})
            before = snapshot(database)
            for payload, line_number in (
                (ndjson(event('pending')) + '\n{bad\n', 3),
                (ndjson(event('pending'), event('two', amount=99)), None),
                (ndjson(event('pending')) + '\n' + ndjson(event('bad', amount=True)), 3),
            ):
                result = cli(database, text=payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
                if line_number is not None:
                    self.assertIn(f'line {line_number}:', result.stderr)
                self.assertEqual(snapshot(database), before)
            for payload in (b'\xff', b'{bad\n'):
                source.write_bytes(payload)
                result = cli(database, source)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
                self.assertEqual(snapshot(database), before)
            result = cli(database, source.with_name('missing.ndjson'))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertTrue(result.stderr.strip())
            self.assertEqual(snapshot(database), before)
            result = cli(pathlib.Path(directory))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertTrue(result.stderr.strip())


unittest.main(argv=['live-codex-sqlite-ledger-verification'])
"""


_SAFE_ZIP_SOURCES = {
    "bundle_unpack/__init__.py": "",
    "bundle_unpack/extract.py": """import zipfile


def extract_archive(archive, destination, max_bytes=1048576):
    with zipfile.ZipFile(archive) as source:
        source.extractall(destination)
        return sorted(source.namelist())
""",
    "bundle_unpack/__main__.py": """import argparse
import json

from .extract import extract_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive')
    parser.add_argument('destination')
    parser.add_argument('--max-bytes', type=int, default=1048576)
    args = parser.parse_args()
    print(json.dumps(extract_archive(args.archive, args.destination, args.max_bytes)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
""",
    "README.md": """# Validated bundle extraction

Repair this standard-library ZIP extraction package for a trusted local destination.
Native zipfile owns archive decoding, decompression and CRC validation; use it.

`bundle_unpack.extract.extract_archive(archive, destination, max_bytes=1048576)`
reads a ZIP and returns a sorted list of extracted relative file paths. Directory
entries are created but omitted from the returned list. Keep binary contents exact
and support Unicode names, nested files, empty files and explicit empty directories.
The archive itself must remain unchanged. Close owned files/archive handles.

The destination must already be a real empty directory. Reject a symlink, file,
missing directory or nonempty destination. This task assumes no concurrent writer;
it does not require adversarial race/custody guarantees or rollback of OS write
failures after validation.

Before the first destination effect, validate the entire archive, including native
decompression/CRC checks. Policy or archive-validation failures must leave the
destination contents unchanged. Use bounded staging if necessary. `max_bytes` must
be a nonnegative integer (not bool). The total uncompressed file bytes may not
exceed it; a zero limit permits empty files/directories. Do not accumulate bytes
past the bound. Validation/policy failures raise ValueError; native archive or I/O
errors may propagate.

Member names are nonempty portable relative POSIX paths. Reject leading slashes,
backslashes, colons, empty/dot/dot-dot components, and repeated separators. One
trailing slash denotes a directory; directory payloads must be empty. Reject
symlink/special-file Unix modes and inconsistent file/directory modes. Ordinary
ZIP entries with unspecified Unix type bits are allowed. Reject duplicate canonical
member paths, including repeated directories, and file/directory prefix conflicts
regardless of archive order. An explicit directory plus its child file is valid.

`python -m bundle_unpack ARCHIVE DEST [--max-bytes N]` prints one JSON list plus
newline on success, exits 0 and emits no stderr. All policy, archive, input and OS
errors exit 2 with a useful diagnostic on stderr and no stdout. Preserve the public
function and CLI. Use only the Python standard library.
""",
}

_SAFE_ZIP_TASK = (
    "Repair the validated ZIP extraction package according to README.md. Use native "
    "zipfile to validate/decode members, validate every member and byte bound before "
    "writing the destination, and fix CLI success/error handling. In particular, reject "
    "both orders of a file/directory prefix conflict: node followed by node/child, or "
    "node/child followed by the regular file node; neither may create output. Keep the public API "
    "and use only the standard library. Before editing, execute the exact "
    "repository_tools.list Hashmarks command from the supplied instructions. Change "
    "only bundle_unpack/extract.py and bundle_unpack/__main__.py. Independent "
    "verification creates its own temporary archives and destinations after your Step."
)

_SAFE_ZIP_VERIFICATION = r"""import json
import pathlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

repository = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repository))
from bundle_unpack.extract import extract_archive


def archive(path, members, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, 'w', compression=compression) as source:
        for name, data in members:
            source.writestr(name, data)
    return path


def cli(source, destination, *arguments):
    return subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('bundle_unpack',run_name='__main__')",
         str(repository), str(source), str(destination), *arguments],
        text=True, capture_output=True, timeout=10,
    )


class BundleExtractionContract(unittest.TestCase):
    def test_valid_binary_unicode_directories_and_exact_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            destination = root / 'output'
            destination.mkdir()
            data = b'\x00\xff\r\n'
            regular = zipfile.ZipInfo('regular.txt')
            regular.create_system = 3
            regular.external_attr = (stat.S_IFREG | 0o644) << 16
            source = archive(root / 'bundle.zip', [
                ('folder/', b''), ('empty/', b''), ('folder/raw.bin', data),
                ('café 雪.txt', 'hèj'.encode('utf-8')), ('empty-file', b''), (regular, b'ok')])
            before = source.read_bytes()
            expected = ['café 雪.txt', 'empty-file', 'folder/raw.bin', 'regular.txt']
            self.assertEqual(extract_archive(source, destination, max_bytes=10), expected)
            self.assertEqual((destination / 'folder/raw.bin').read_bytes(), data)
            self.assertEqual((destination / 'café 雪.txt').read_bytes(), 'hèj'.encode('utf-8'))
            self.assertEqual((destination / 'empty-file').read_bytes(), b'')
            self.assertEqual((destination / 'regular.txt').read_bytes(), b'ok')
            self.assertTrue((destination / 'empty').is_dir())
            self.assertEqual(sorted(path.relative_to(destination).as_posix()
                                    for path in destination.rglob('*') if path.is_file()), expected)
            self.assertEqual(source.read_bytes(), before)

    def test_child_before_explicit_parent_directory_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            destination = root / 'output'
            destination.mkdir()
            source = archive(root / 'bundle.zip', [('folder/file', b'ok'), ('folder/', b'')])
            self.assertEqual(extract_archive(source, destination), ['folder/file'])
            self.assertEqual((destination / 'folder/file').read_bytes(), b'ok')

    def test_default_byte_limit_rejects_oversize_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            destination = root / 'output'
            destination.mkdir()
            source = archive(root / 'bundle.zip', [('first', b'ok'), ('oversize', b'x' * (1048576 + 1))])
            with self.assertRaises(ValueError):
                extract_archive(source, destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_empty_archive_and_zero_byte_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for index, members in enumerate(([], [('zero', b''), ('empty/', b'')])):
                destination = root / str(index)
                destination.mkdir()
                source = archive(root / f'{index}.zip', members)
                self.assertEqual(extract_archive(source, destination, max_bytes=0),
                                 [] if index == 0 else ['zero'])

    def test_late_invalid_names_leave_destination_and_outside_unchanged(self):
        invalid = ['../outside.txt', '/absolute.txt', 'a/../x', './x', 'a/./x',
                   'a//x', 'a\\x', 'C:drive.txt', 'a:stream', '.', '..', 'a//']
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            outside = root / 'outside.txt'
            outside.write_bytes(b'keep')
            for index, name in enumerate(invalid):
                with self.subTest(name=name):
                    destination = root / f'out{index}'
                    destination.mkdir()
                    source = archive(root / f'{index}.zip', [('first.txt', b'good'), (name, b'bad')])
                    before = source.read_bytes()
                    with self.assertRaises(ValueError):
                        extract_archive(source, destination)
                    self.assertEqual(list(destination.iterdir()), [])
                    self.assertEqual(outside.read_bytes(), b'keep')
                    self.assertEqual(source.read_bytes(), before)

    def test_duplicate_and_prefix_conflicts_are_order_independent(self):
        cases = [
            [('same', b'a'), ('same', b'b')],
            [('same/', b''), ('same/', b'')],
            [('node', b'a'), ('node/child', b'b')],
            [('node/child', b'b'), ('node', b'a')],
            [('node/', b''), ('node', b'a')],
            [('node', b'a'), ('node/', b'')],
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for index, members in enumerate(cases):
                with self.subTest(members=members):
                    destination = root / f'out{index}'
                    destination.mkdir()
                    source = root / f'{index}.zip'
                    if index < 2:
                        with self.assertWarnsRegex(UserWarning, 'Duplicate name'):
                            archive(source, members)
                    else:
                        archive(source, members)
                    with self.assertRaises(ValueError):
                        extract_archive(source, destination)
                    self.assertEqual(list(destination.iterdir()), [])

    def test_unix_special_modes_and_directory_payload_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            cases = [(stat.S_IFLNK | 0o777, 'link', b'../outside'),
                     (stat.S_IFIFO | 0o600, 'fifo', b''),
                     (stat.S_IFDIR | 0o755, 'wrong', b''),
                     (stat.S_IFREG | 0o644, 'wrong/', b''),
                     (stat.S_IFDIR | 0o755, 'directory/', b'payload')]
            for index, (mode, name, data) in enumerate(cases):
                with self.subTest(mode=mode, name=name):
                    member = zipfile.ZipInfo(name)
                    member.create_system = 3
                    member.external_attr = mode << 16
                    destination = root / f'out{index}'
                    destination.mkdir()
                    source = archive(root / f'{index}.zip', [('first', b'good'), (member, data)])
                    with self.assertRaises(ValueError):
                        extract_archive(source, destination)
                    self.assertEqual(list(destination.iterdir()), [])

    def test_invalid_limits_and_total_uncompressed_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = archive(root / 'bundle.zip', [('first', b'123456'), ('last', b'abcdef')])
            empty = archive(root / 'empty.zip', [])
            for index, limit in enumerate([-1, True, False, 1.5, '12', None, 0, 6, 11]):
                with self.subTest(limit=limit):
                    destination = root / f'out{index}'
                    destination.mkdir()
                    with self.assertRaises(ValueError):
                        extract_archive(empty if index < 6 else source, destination, max_bytes=limit)
                    self.assertEqual(list(destination.iterdir()), [])

    def test_native_crc_failure_is_detected_before_destination_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = archive(root / 'bad.zip', [('first', b'good'), ('last', b'corrupt-me')],
                             compression=zipfile.ZIP_STORED)
            with zipfile.ZipFile(source) as native:
                self.assertIsNone(native.testzip())
                offset = native.getinfo('last').header_offset
            data = bytearray(source.read_bytes())
            name_size, extra_size = struct.unpack_from('<HH', data, offset + 26)
            data[offset + 30 + name_size + extra_size] ^= 1
            source.write_bytes(data)
            destination = root / 'output'
            destination.mkdir()
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                extract_archive(source, destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_destination_must_be_real_existing_and_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = archive(root / 'bundle.zip', [('keep.txt', b'replace')])
            nonempty = root / 'nonempty'
            nonempty.mkdir()
            keep = nonempty / 'keep.txt'
            keep.write_bytes(b'original')
            target_file = root / 'file'
            target_file.write_bytes(b'original')
            missing = root / 'missing'
            for destination in (nonempty, target_file, missing):
                with self.subTest(destination=destination), self.assertRaises((ValueError, OSError)):
                    extract_archive(source, destination)
            self.assertEqual(keep.read_bytes(), b'original')
            self.assertEqual(target_file.read_bytes(), b'original')
            self.assertFalse(missing.exists())

    def test_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = archive(root / 'bundle.zip', [('file', b'content')])
            real = root / 'real'
            real.mkdir()
            link = root / 'link'
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises((ValueError, OSError)):
                extract_archive(source, link)
            self.assertEqual(list(real.iterdir()), [])
            self.assertTrue(link.is_symlink())

    def test_cli_unicode_success_and_atomic_error_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = archive(root / 'café bundle.zip', [('雪.bin', b'\x00\xff')])
            destination = root / 'output with spaces'
            destination.mkdir()
            result = cli(source, destination, '--max-bytes', '2')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertTrue(result.stdout.endswith('\n'))
            self.assertEqual(json.loads(result.stdout), ['雪.bin'])
            self.assertEqual((destination / '雪.bin').read_bytes(), b'\x00\xff')
            bad = archive(root / 'bad.zip', [('first', b'ok'), ('../late', b'no')])
            malformed = root / 'malformed.zip'
            malformed.write_bytes(b'not a zip')
            cases = [(bad, []), (malformed, []), (source, ['--max-bytes', '1']),
                     (source.with_name('missing.zip'), []), (source, ['--max-bytes', '-1'])]
            for index, (path, arguments) in enumerate(cases):
                target = root / f'bad-output{index}'
                target.mkdir()
                result = cli(path, target, *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
                self.assertEqual(list(target.iterdir()), [])


unittest.main(argv=['live-codex-zip-extraction-verification'])
"""


_HTTP_NODE_SOURCES: dict[str, str] = {
    "byte_asset/range.mjs": """export function parseRange(header, size) {
    return null;
}
""",
    "byte_asset/handler.mjs": """import { parseRange } from './range.mjs';

export function createHandler(body, pathname = '/download') {
    return (request, response) => {
        if (request.url !== pathname) {
            response.writeHead(404);
            response.end();
            return;
        }
        parseRange(request.headers.range, body.length);
        const text = body.toString('utf8');
        response.writeHead(200, { 'Content-Length': text.length });
        response.end(text);
    };
}
""",
    "README.md": (
        "# Byte asset\n\nA dependency-free Node.js ESM handler serving an in-memory binary "
        "download with a deliberately limited single-range contract.\n"
    ),
}

_HTTP_NODE_TASK = """Repair byte_asset/range.mjs and byte_asset/handler.mjs using only native
Node.js standard libraries. Keep their named public exports and change only those two files.
Before editing, execute the exact repository_tools.list Hashmarks command supplied in the
instructions and read the captured Skill resource. Implement this limited application contract:

parseRange(header, size) validates size first: it must be a nonnegative safe integer; reject
invalid sizes with TypeError or RangeError even when the header is absent. Missing, nonstring,
malformed or unsupported headers return null (ignore the range). Recognize only exact lowercase
ASCII bytes=START-END, bytes=START-, bytes=-SUFFIX with at least one decimal number. Leading
zeros are allowed; spaces, commas, trailing characters/linebreaks and unsafe integer fields
are malformed. Return exactly {start, end}, inclusive. Clamp an oversized end to size-1.
Open ranges end at size-1. Suffix ranges select the last SUFFIX bytes, bounded by the full body.
For syntactically valid safe-integer fields, start>=size, end<start, zero suffix and any valid
range on an empty body throw RangeError. This is an application contract, not full HTTP RFC support.

createHandler(body, pathname='/download') accepts a Buffer only and captures a copy when
created, so later caller mutation cannot alter responses. pathname must be a string starting
with / and containing no query or fragment; reject invalid factory inputs. Use Node's URL
parser for request paths, ignore query strings, and match the configured pathname exactly.
Unknown paths return 404, Content-Length: 0, empty body. Known paths support GET and HEAD only;
other methods return 405, Allow: GET, HEAD, Content-Length: 0, empty body.
GET without a range, or with an ignored range, returns 200 with all original binary bytes.
Satisfiable ranges return 206 with exactly the selected bytes and Content-Range: bytes S-E/N.
Unsatisfiable ranges return 416, Content-Range: bytes */N, Content-Length: 0, empty body.
Every known-path GET/HEAD response has Accept-Ranges: bytes; successful 200/206 responses
have Content-Type: application/octet-stream and exact byte Content-Length. Full responses
have no Content-Range. HEAD ignores Range entirely, returning 200 and full headers but no body.
Empty buffers and custom paths must work. Do not use string lengths or decode binary bodies.

The coding Step has no network capability. Use pure function and native syntax self-checks;
independent verification runs real loopback HTTP requests after the coding Step completes.
"""

_HTTP_NODE_VERIFICATION = r"""import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.argv[2];
const { parseRange } = await import(pathToFileURL(path.join(root, 'byte_asset/range.mjs')));
const { createHandler } = await import(pathToFileURL(path.join(root, 'byte_asset/handler.mjs')));
for (const [header, size, expected] of [
    ['bytes=1-3', 8, {start: 1, end: 3}],
    ['bytes=6-', 8, {start: 6, end: 7}],
    ['bytes=-3', 8, {start: 5, end: 7}],
    ['bytes=-99', 8, {start: 0, end: 7}],
    ['bytes=0001-0002', 8, {start: 1, end: 2}],
    ['bytes=0-9007199254740991', 8, {start: 0, end: 7}],
    ['bytes=0-0', 1, {start: 0, end: 0}],
]) assert.deepEqual(parseRange(header, size), expected);
const ignored = [undefined, null, 3, {}, '', 'Bytes=0-1', 'bytes=', 'bytes=-',
    'bytes= 0-1', 'bytes=0 -1', 'bytes=0-1 ', 'bytes=0-1\n', 'bytes=0-1\r\n',
    'bytes=0-1,3-4', 'bytes=+0-1', 'bytes=0-1x', 'bytes=\u0660-1',
    'bytes=0-9007199254740992', 'bytes=9007199254740992-', 'bytes=-9007199254740992'];
for (const header of ignored) assert.equal(parseRange(header, 8), null);
for (const header of ['bytes=8-', 'bytes=9-10', 'bytes=4-3', 'bytes=-0']) {
    assert.throws(() => parseRange(header, 8), RangeError);
}
for (const header of ['bytes=0-0', 'bytes=0-', 'bytes=-1']) {
    assert.throws(() => parseRange(header, 0), RangeError);
}
for (const size of [-1, 1.5, NaN, Infinity, 9007199254740992, '8', null, undefined]) {
    assert.throws(() => parseRange(undefined, size), error =>
        error instanceof TypeError || error instanceof RangeError);
}
assert.equal(parseRange(undefined, 0), null);
for (const body of ['text', new Uint8Array([1]), null]) {
    assert.throws(() => createHandler(body));
}
for (const pathname of ['', 'download', '/download?x=1', '/download#x', null, 3]) {
    assert.throws(() => createHandler(Buffer.alloc(0), pathname));
}

async function withServer(handler, inspect) {
    const server = http.createServer(handler);
    const sockets = new Set();
    server.on('connection', socket => {
        sockets.add(socket);
        socket.on('close', () => sockets.delete(socket));
    });
    try {
        await new Promise((resolve, reject) => {
            server.once('error', reject);
            server.listen(0, '127.0.0.1', resolve);
        });
        const address = server.address();
        await inspect((method = 'GET', target = '/download', range) =>
            new Promise((resolve, reject) => {
                const request = http.request({host: '127.0.0.1', port: address.port,
                    method, path: target, agent: false,
                    headers: range === undefined ? {} : {Range: range}}, response => {
                    const chunks = [];
                    let length = 0;
                    response.on('data', chunk => {
                        length += chunk.length;
                        if (length > 1024) response.destroy(new Error('Unbounded response'));
                        else chunks.push(chunk);
                    });
                    response.on('error', reject);
                    response.on('aborted', () => reject(new Error('Aborted response')));
                    response.on('end', () => resolve({status: response.statusCode,
                        headers: response.headers, body: Buffer.concat(chunks)}));
                });
                request.setTimeout(2000, () => request.destroy(new Error('Request timeout')));
                request.on('error', reject);
                request.end();
            }));
    } finally {
        for (const socket of sockets) socket.destroy();
        await new Promise(resolve => server.close(resolve));
    }
}
function success(response, status, bytes, total, contentRange) {
    assert.equal(response.status, status);
    assert.equal(response.headers['accept-ranges'], 'bytes');
    assert.equal(response.headers['content-type'], 'application/octet-stream');
    assert.equal(response.headers['content-length'], String(total));
    assert.equal(response.headers['content-range'], contentRange);
    assert.deepEqual(response.body, bytes);
}
const original = Buffer.from([0, 255, 195, 169, 10, 128, 42, 13]);
const supplied = Buffer.from(original);
const handler = createHandler(supplied);
supplied.fill(7);
await withServer(handler, async request => {
    success(await request(), 200, original, original.length);
    success(await request('GET', '/download?name=caf%C3%A9'), 200, original, original.length);
    for (const range of ['units=0-1', 'bytes=0-1,3-4', 'bytes= 0-1',
        'bytes=0-9007199254740992']) {
        success(await request('GET', '/download', range), 200, original, original.length);
    }
    for (const [range, start, end] of [['bytes=1-3', 1, 3], ['bytes=6-', 6, 7],
        ['bytes=-3', 5, 7], ['bytes=-99', 0, 7], ['bytes=0-99', 0, 7]]) {
        success(await request('GET', '/download', range), 206,
            original.subarray(start, end + 1), end - start + 1, `bytes ${start}-${end}/8`);
    }
    for (const range of ['bytes=8-', 'bytes=4-3', 'bytes=-0']) {
        const response = await request('GET', '/download', range);
        assert.equal(response.status, 416);
        assert.equal(response.headers['accept-ranges'], 'bytes');
        assert.equal(response.headers['content-range'], 'bytes */8');
        assert.equal(response.headers['content-length'], '0');
        assert.equal(response.body.length, 0);
    }
    for (const range of [undefined, 'bytes=8-', 'bytes=1-2']) {
        success(await request('HEAD', '/download', range), 200, Buffer.alloc(0), 8);
    }
    for (const [method, target, status] of [['GET', '/missing', 404],
        ['GET', '/download/extra', 404], ['POST', '/download', 405],
        ['DELETE', '/download?x=1', 405], ['POST', '/missing', 404]]) {
        const response = await request(method, target);
        assert.equal(response.status, status);
        assert.equal(response.headers['content-length'], '0');
        assert.equal(response.body.length, 0);
        if (status === 405) assert.equal(response.headers.allow, 'GET, HEAD');
    }
});
await withServer(createHandler(Buffer.alloc(0)), async request => {
    success(await request(), 200, Buffer.alloc(0), 0);
    const response = await request('GET', '/download', 'bytes=0-');
    assert.equal(response.status, 416);
    assert.equal(response.headers['content-range'], 'bytes */0');
    assert.equal(response.headers['content-length'], '0');
    assert.equal(response.headers['accept-ranges'], 'bytes');
    assert.equal(response.body.length, 0);
});
await withServer(createHandler(original, '/asset'), async request => {
    success(await request('GET', '/asset?cache=1'), 200, original, 8);
    assert.equal((await request()).status, 404);
});
console.log('Binary HTTP range contract verified through native Node');
"""


_FRAMES_NODE_SOURCES: dict[str, str] = {
    "telemetry/codec.mjs": """export function encodeFrame(value) {
    const text = JSON.stringify(value);
    const header = Buffer.alloc(4);
    header.writeUInt32BE(text.length);
    return Buffer.concat([header, Buffer.from(text)]);
}

export async function* decodeFrames(source, maxBytes = 1048576) {
    for await (const chunk of source) {
        yield JSON.parse(chunk.subarray(4).toString('utf8'));
    }
}
""",
    "telemetry/cli.mjs": """import fs from 'node:fs';
import { decodeFrames } from './codec.mjs';

try {
    const source = process.argv[2] ? fs.createReadStream(process.argv[2]) : process.stdin;
    for await (const value of decodeFrames(source)) console.log(value);
} catch (error) {
    console.error(error.message);
    process.exitCode = 1;
}
""",
    "README.md": (
        "# Framed telemetry\n\nA dependency-free ESM codec and streaming CLI for "
        "four-byte big-endian length-prefixed UTF-8 JSON messages.\n"
    ),
}

_FRAMES_NODE_TASK = """Repair telemetry/codec.mjs and telemetry/cli.mjs using only native Node.js
standard libraries. Keep their exports and change only those two files. Before editing,
execute the exact repository_tools.list Hashmarks command supplied in the instructions
and read the frozen Skill resource. Implement this application protocol:

encodeFrame(value) uses native JSON.stringify semantics. Reject values whose serialization
is undefined (such as undefined/functions/symbols), and propagate native serialization
errors. Return one Buffer: four-byte unsigned big-endian payload BYTE length followed by
the UTF-8 JSON bytes. Unicode length must be byte length, not JavaScript string length.

decodeFrames(source, maxBytes=1048576) is an async generator over an async or sync iterable
of Buffer/Uint8Array chunks. Respect typed-array byteOffset/byteLength; reject other chunk
types with TypeError. Never modify supplied chunks. Yield one native JSON.parse value per
frame, in order, including null, booleans, strings, arrays and objects. This generator API
can yield null normally; do not substitute Node stream EOF semantics for JSON values.
maxBytes must be an integer from 1 through 4294967295; reject invalid limits with TypeError
or RangeError on the first pull, before consuming source input. A header length of zero or
greater than maxBytes throws RangeError immediately after the four header bytes arrive,
without pulling another source chunk for that frame. Support empty chunks, every possible
header/payload split, and multiple frames in one chunk. Decode strict UTF-8 without
replacement, reject BOM-prefixed JSON, and let native JSON.parse own JSON syntax/values.
Clean EOF between frames is valid; incomplete headers or payloads at EOF are errors.
Complete earlier messages may be yielded before a later error; never yield the invalid
frame or subsequent frames. Yield each complete frame before examining a later frame in
the same chunk, and before pulling another source chunk. Retain only an unfinished tail
between source pulls, rather than accumulating decoded history. Use native async-iteration
lifetime semantics: consumer early return and parser errors must close upstream iteration
and await its cleanup; upstream errors must propagate without being replaced by EOF errors.

CLI: node telemetry/cli.mjs [INPUT] reads the optional binary file or stdin, accepts at
most one file argument, and writes native JSON.stringify(value) plus newline for each
decoded message. Success exits 0 with no stderr. Usage, file, decoding or stream errors
exit 2 with a diagnostic on stderr. Preserve complete earlier output lines on late error;
write no invalid/later record, and handle native stdout backpressure/flush correctly.
Repeated stdout drains must not retain obsolete listeners or produce listener warnings.
No npm dependencies, network access, package manifests or extra source files are needed.
Independent verification runs after your coding Step using the selected public Node.
"""

_FRAMES_NODE_VERIFICATION = r"""import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = process.argv[2];
const { encodeFrame, decodeFrames } = await import(
    pathToFileURL(path.join(root, 'telemetry/codec.mjs')));
function wire(payload) {
    const bytes = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
    const header = Buffer.alloc(4);
    header.writeUInt32BE(bytes.length);
    return Buffer.concat([header, bytes]);
}
async function collect(source, maxBytes) {
    const values = [];
    try {
        for await (const value of decodeFrames(source, maxBytes)) values.push(value);
        return {values};
    } catch (error) { return {values, error}; }
}
const values = [null, false, 0, 'café 雪 🌍', [1, null, 'x'],
    {device: 'α', reading: 2.5}, JSON.parse('{"__proto__":{"ok":true}}'), '\ud800'];
for (const value of values) {
    const text = JSON.stringify(value);
    const encoded = encodeFrame(value);
    assert(Buffer.isBuffer(encoded));
    assert.equal(encoded.readUInt32BE(0), Buffer.byteLength(text));
    assert.deepEqual(encoded, wire(text));
}
for (const value of [undefined, () => 1, Symbol('x'), 1n]) {
    assert.throws(() => encodeFrame(value), TypeError);
}
const cyclic = {}; cyclic.self = cyclic;
assert.throws(() => encodeFrame(cyclic), TypeError);
const packet = Buffer.concat(values.map(value => wire(JSON.stringify(value))));
const before = Buffer.from(packet);
for (let split = 0; split <= packet.length; split++) {
    const chunks = [Buffer.alloc(0), packet.subarray(0, split),
        new Uint8Array(0), packet.subarray(split)];
    assert.deepEqual(await collect(chunks), {values});
}
assert.deepEqual(await collect([...packet].map(byte => Buffer.from([byte]))), {values});
const wrapped = Buffer.concat([Buffer.from([255]), packet, Buffer.from([255])]);
const view = new Uint8Array(wrapped.buffer, wrapped.byteOffset + 1, packet.length);
assert.deepEqual(await collect([view]), {values});
assert.deepEqual(packet, before);
assert.deepEqual(wrapped.subarray(1, -1), packet);
assert.deepEqual(await collect([]), {values: []});
assert.deepEqual(await collect([wire(' \n-0\t')]), {values: [-0]});
assert.deepEqual(await collect([wire('null')], 4), {values: [null]});
assert.deepEqual(await collect([wire('0')], 1), {values: [0]});
assert.deepEqual(await collect([wire('null')], 4294967295), {values: [null]});
for (const maxBytes of [0, -1, 1.5, NaN, Infinity, 4294967296, '64', null]) {
    let pulled = false;
    async function* unused() { pulled = true; yield packet; }
    const result = await collect(unused(), maxBytes);
    assert(result.error instanceof TypeError || result.error instanceof RangeError);
    assert.equal(pulled, false);
    assert.deepEqual(result.values, []);
}
for (const [length, maxBytes] of [[0, 32], [33, 32], [1048577, undefined]]) {
    let pulledPayload = false, closed = 0;
    const header = Buffer.alloc(4); header.writeUInt32BE(length);
    async function* bounded() {
        try { yield header; pulledPayload = true; yield Buffer.alloc(length); }
        finally { await Promise.resolve(); closed++; }
    }
    const result = await collect(bounded(), maxBytes);
    assert(result.error instanceof RangeError);
    assert.deepEqual(result.values, []);
    assert.equal(pulledPayload, false);
    assert.equal(closed, 1);
}
const prefix = wire('{"ok":true}');
const invalid = [wire(Buffer.from([34, 195, 40, 34])), wire('falsex'),
    wire(Buffer.concat([Buffer.from([239, 187, 191]), Buffer.from('null')])),
    ...[1, 2, 3].map(n => Buffer.alloc(n)), wire('"unfinished"').subarray(0, -1)];
for (const bad of invalid) {
    const result = await collect([Buffer.concat([prefix, bad])]);
    assert.deepEqual(result.values, [{ok: true}]);
    assert(result.error instanceof Error);
}
for (const bad of ['text', [1, 2], new Uint16Array([1]), null]) {
    let closed = 0;
    async function* wrongType() {
        try { yield prefix; yield bad; yield prefix; }
        finally { await Promise.resolve(); closed++; }
    }
    const result = await collect(wrongType());
    assert.deepEqual(result.values, [{ok: true}]);
    assert(result.error instanceof TypeError);
    assert.equal(closed, 1);
}
for (const cancel of [false, true]) {
    let pulledNext = false, closed = 0;
    async function* lazy() {
        try {
            yield Buffer.concat([prefix, Buffer.alloc(4)]);
            pulledNext = true; yield prefix;
        } finally { await new Promise(resolve => setTimeout(resolve, 2)); closed++; }
    }
    const decoder = decodeFrames(lazy());
    assert.deepEqual(await decoder.next(), {value: {ok: true}, done: false});
    assert.equal(pulledNext, false);
    if (cancel) await decoder.return();
    else await assert.rejects(() => decoder.next(), RangeError);
    assert.equal(closed, 1);
    assert.equal(pulledNext, false);
}
const upstreamError = new Error('upstream canary');
async function* failingSource() { yield Buffer.alloc(2); throw upstreamError; }
assert.equal((await collect(failingSource())).error, upstreamError);

const scratch = fs.mkdtempSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'frames-'));
try {
    const cli = path.join(root, 'telemetry/cli.mjs');
    const run = (input, args = []) => spawnSync(process.execPath, [cli, ...args],
        {cwd: root, input, encoding: 'utf8', timeout: 10000, maxBuffer: 8 * 1024 * 1024});
    const output = values.map(value => JSON.stringify(value) + '\n').join('');
    const stdin = run(packet);
    assert.equal(stdin.status, 0, stdin.stderr);
    assert.equal(stdin.stdout, output);
    assert.equal(stdin.stderr, '');
    const file = path.join(scratch, 'café telemetry.bin');
    fs.writeFileSync(file, packet);
    const fromFile = run(undefined, [file]);
    assert.equal(fromFile.status, 0, fromFile.stderr);
    assert.equal(fromFile.stdout, output);
    assert.equal(fromFile.stderr, '');
    const empty = run(Buffer.alloc(0));
    assert.equal(empty.status, 0, empty.stderr);
    assert.equal(empty.stdout, '');
    assert.equal(empty.stderr, '');
    for (const args of [[path.join(scratch, 'missing')], [file, file]]) {
        const result = run(Buffer.alloc(0), args);
        assert.equal(result.status, 2);
        assert.equal(result.stdout, '');
        assert(result.stderr.trim());
    }
    const big = '雪'.repeat(100000);
    const late = run(Buffer.concat([wire(JSON.stringify(big)), Buffer.alloc(4), prefix]));
    assert.equal(late.status, 2);
    assert.equal(late.stdout, JSON.stringify(big) + '\n');
    assert(late.stderr.trim());
    const repeated = run(Buffer.concat(Array(16).fill(wire(JSON.stringify(big)))));
    assert.equal(repeated.status, 0, repeated.stderr);
    assert.equal(repeated.stdout, (JSON.stringify(big) + '\n').repeat(16));
    assert.equal(repeated.stderr, '');
} finally { fs.rmSync(scratch, {recursive: true, force: true}); }
console.log('Framed JSON stream, cancellation and binary CLI contract verified');
"""


_DOCUMENT_PATCH_SOURCES: dict[str, str] = {
    "docpatch/__init__.py": "",
    "docpatch/pointer.py": """def parse_pointer(path):
    return path.strip('/').split('/')
""",
    "docpatch/patch.py": """from .pointer import parse_pointer


def apply_patch(document, operations):
    for operation in operations:
        key = parse_pointer(operation['path'])[-1]
        if operation['op'] == 'remove':
            document.pop(key, None)
        elif operation['op'] != 'test':
            document[key] = operation.get('value')
    return document
""",
    "docpatch/__main__.py": """import json
import sys

from .patch import apply_patch

with open(sys.argv[1]) as document, open(sys.argv[2]) as patch:
    print(json.dumps(apply_patch(json.load(document), json.load(patch))))
""",
    "README.md": """# Document patch subset

Repair this standard-library JSON configuration patcher. The supported operations
are add, remove, replace and test; other operations are errors. This is a limited
application contract, with JSON Pointer syntax and the named operation semantics
from https://www.rfc-editor.org/rfc/rfc6901 and https://www.rfc-editor.org/rfc/rfc6902.
Root removal is deliberately rejected; add/replace/test at the root are supported.

docpatch.pointer.parse_pointer(path) returns a tuple of decoded tokens. The empty
string selects the root; otherwise a string starts with /. Decode ~1 as slash and
~0 as tilde exactly once: ~01 means the literal ~1. Reject dangling/other tilde
escapes and non-string paths with ValueError. Empty tokens, Unicode and NUL keys
are allowed. Do not decode percent escapes, URI fragments or normalize Unicode.

docpatch.patch.apply_patch(document, operations) returns a detached new JSON tree.
Inputs are JSON-compatible trees with finite numbers. Never mutate document,
operation dictionaries or their values, including on failure or an empty patch.
Copy each inserted/replacement value independently. Consume an iterable of
operations once, in order; stop at its first invalid operation and propagate
iterator failures. Every operation is a dict with string op/path; add/replace/test
require a value member, where null is valid. Ignore unrelated members.

Traverse existing dict/list parents only; do not create parents. For dicts, add
creates or replaces a key; remove/replace/test require that key to exist. Any
string token (including empty, 00 or -) is an ordinary object key. For lists,
indices are ASCII 0 or a nonzero digit followed by ASCII digits. Add inserts at
0..len or appends with -; other operations require 0..len-1. Reject signs,
leading zeros, whitespace, Unicode digits, missing parents and scalar traversal.
Test compares JSON types recursively: booleans differ from numbers, numbers
compare numerically (1 equals 1.0), object order is immaterial, and arrays retain
order. Invalid operations/addresses, root removal and failed tests raise ValueError.

python -m docpatch DOCUMENT PATCH reads UTF-8 JSON files; PATCH=- reads stdin.
The CLI requires exactly two arguments and a patch array. Native json owns JSON
decoding/encoding. Reject every non-finite decoded number, including exponent
overflow, ignored fields and values later overwritten by duplicate keys. Finite
duplicate keys retain native json last-value behavior. Serialize a complete finite
result before writing stdout. Success prints one JSON value plus newline, exits 0
and has no stderr. Input, policy and serialization errors exit 2, diagnose stderr
and leave stdout empty. Never rewrite input files. No third-party dependencies.
""",
}

_DOCUMENT_PATCH_TASK = (
    "Repair the JSON document patch package according to README.md. Preserve the "
    "public parse_pointer and apply_patch functions and module CLI. Handle escaped "
    "pointers, sequential array/object operations, detached results, recursive JSON "
    "equality and failure atomicity. Use native json and only the Python standard "
    "library. Before editing, execute the exact repository_tools.list Hashmarks "
    "command from the supplied instructions and read the frozen Skill resource. "
    "Change only docpatch/pointer.py, docpatch/patch.py and docpatch/__main__.py. "
    "Independent verification runs after your coding Step."
)

_DOCUMENT_PATCH_VERIFICATION = r"""import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from docpatch.pointer import parse_pointer
from docpatch.patch import apply_patch


class DocumentPatchContract(unittest.TestCase):
    def test_pointer_decoding_and_literal_keys(self):
        cases = {'': (), '/': ('',), '//': ('', ''), '/a~1b/m~0n': ('a/b', 'm~n'),
                 '/~01': ('~1',), '/~00': ('~0',), '/~1~0': ('/~',),
                 '/%2F/雪/\x00': ('%2F', '雪', '\x00'), '/00': ('00',)}
        for pointer, expected in cases.items():
            with self.subTest(pointer=pointer):
                self.assertEqual(parse_pointer(pointer), expected)
        for pointer in (None, 1, False, [], {}, 'a', '#/a', '/a~', '/~2', '/~0~9'):
            with self.subTest(pointer=pointer):
                with self.assertRaises(ValueError):
                    parse_pointer(pointer)

    def test_ordered_mixed_edits_and_input_nonmutation(self):
        document = {'services': [{'id': 'α', 'enabled': False}, {'id': 'b'}],
                    'meta': {'owner': 'old'}}
        operations = [
            {'op': 'test', 'path': '/services/0/enabled', 'value': False},
            {'op': 'add', 'path': '/services/1', 'value': {'id': 'new', 'flags': []}},
            {'op': 'replace', 'path': '/services/2/id', 'value': 'β'},
            {'op': 'add', 'path': '/services/-', 'value': None},
            {'op': 'add', 'path': '/meta/owner', 'value': 'updated'},
            {'op': 'test', 'path': '/meta/owner', 'value': 'updated'},
            {'op': 'remove', 'path': '/meta/owner'},
            {'op': 'add', 'path': '/meta/new', 'value': {'v': 1}},
            {'op': 'test', 'path': '/meta/new', 'value': {'v': 1.0}},
        ]
        before = copy.deepcopy((document, operations))
        result = apply_patch(document, operations)
        self.assertEqual(result, {'services': [{'id': 'α', 'enabled': False},
            {'id': 'new', 'flags': []}, {'id': 'β'}, None], 'meta': {'new': {'v': 1}}})
        self.assertEqual((document, operations), before)
        result['services'][1]['flags'].append('changed')
        result['services'][0]['id'] = 'changed'
        self.assertEqual((document, operations), before)

    def test_independent_insertions_and_empty_patch(self):
        value = {'nested': []}
        operations = [{'op': 'add', 'path': '/' + name, 'value': value}
                      for name in ('left', 'right')]
        result = apply_patch({}, operations)
        result['left']['nested'].append(1)
        self.assertEqual(result['right'], {'nested': []})
        self.assertEqual(value, {'nested': []})
        original = {'nested': []}
        detached = apply_patch(original, [])
        detached['nested'].append(1)
        self.assertEqual(original, {'nested': []})
        replacement = {'nested': []}
        result = apply_patch({'target': 0}, [
            {'op': 'replace', 'path': '/target', 'value': replacement}])
        result['target']['nested'].append(1)
        self.assertEqual(replacement, {'nested': []})

    def test_root_operations_and_existing_null(self):
        for value in (None, False, 7, 'root', [1], {'nested': []}):
            for operation in ('add', 'replace'):
                result = apply_patch(None, [{'op': operation, 'path': '', 'value': value}])
                self.assertEqual(result, value)
        for operation in ('add', 'replace'):
            for value in ([{'nested': []}], {'nested': []}):
                document = {'old': []}
                before = copy.deepcopy(value)
                result = apply_patch(document, [{'op': operation, 'path': '', 'value': value}])
                (result[0]['nested'] if isinstance(result, list) else result['nested']).append(1)
                self.assertEqual(value, before)
                self.assertEqual(document, {'old': []})
        self.assertIsNone(apply_patch(None, [{'op': 'test', 'path': '', 'value': None}]))
        self.assertEqual(apply_patch(0, [{'op': 'replace', 'path': '', 'value': []},
                                       {'op': 'add', 'path': '/0', 'value': 'x'}]), ['x'])
        self.assertEqual(apply_patch({'x': None}, [{'op': 'test', 'path': '/x', 'value': None},
                                                  {'op': 'remove', 'path': '/x'}]), {})
        with self.assertRaises(ValueError):
            apply_patch({}, [{'op': 'test', 'path': '/x', 'value': None}])
        with self.assertRaises(ValueError):
            apply_patch({}, [{'op': 'remove', 'path': ''}])

    def test_object_keys_are_not_array_indices(self):
        document = {'': {'a/b': {'~1': 0}}, '-': 1, '00': 2, '%2F': 3,
                    'é': 4, 'e\u0301': 5, '\x00': 6}
        operations = [{'op': 'replace', 'path': path, 'value': index}
                      for index, path in enumerate(('//a~1b/~01', '/-', '/00', '/%2F',
                                                    '/e\u0301', '/\x00'), 10)]
        result = apply_patch(document, operations)
        self.assertEqual(result, {'': {'a/b': {'~1': 10}}, '-': 11, '00': 12,
                                 '%2F': 13, 'é': 4, 'e\u0301': 14, '\x00': 15})

    def test_array_boundaries_and_duplicate_operations(self):
        operations = [{'op': 'add', 'path': '/0', 'value': 'a'},
                      {'op': 'add', 'path': '/0', 'value': 'b'},
                      {'op': 'add', 'path': '/2', 'value': 'c'},
                      {'op': 'remove', 'path': '/1'},
                      {'op': 'test', 'path': '/1', 'value': 'c'}]
        self.assertEqual(apply_patch([], operations), ['b', 'c'])
        for token in ('00', '01', '-1', '+1', ' 0', '0 ', '0\n', '١', '０', '', '9' * 60):
            for operation in ('add', 'remove', 'replace', 'test'):
                with self.subTest(token=token, operation=operation), self.assertRaises(ValueError):
                    apply_patch([0], [{'op': operation, 'path': '/' + token, 'value': 0}])
        for operation in ('remove', 'replace', 'test'):
            for token in ('1', '-'):
                with self.subTest(token=token, operation=operation), self.assertRaises(ValueError):
                    apply_patch([0], [{'op': operation, 'path': '/' + token, 'value': 0}])

    def test_recursive_json_equality(self):
        same = [({'a': [1, {'flag': False}]}, {'a': [1.0, {'flag': False}]}),
                ({'a': 1, 'b': 2}, {'b': 2.0, 'a': 1.0})]
        for document, value in same:
            self.assertEqual(apply_patch(document, [{'op': 'test', 'path': '', 'value': value}]),
                             document)
        different = [(True, 1), (False, 0), ([{'flag': True}], [{'flag': 1}]),
                     ({'a': [False]}, {'a': [0]}), ([1, 2], [2, 1]),
                     ({'a': None}, {}), ('1', 1)]
        for document, value in different:
            with self.subTest(document=document, value=value), self.assertRaises(ValueError):
                apply_patch(document, [{'op': 'test', 'path': '', 'value': value}])

    def test_invalid_operations_are_atomic(self):
        bad = [None, [], 'add', {}, {'op': 'add'}, {'path': '/new'},
               {'op': True, 'path': '/new'}, {'op': [], 'path': '/new'},
               {'op': {}, 'path': '/new'}, {'op': 'add', 'path': None, 'value': 0},
               {'op': 'copy', 'path': '/new', 'from': '/x'},
               {'op': 'move', 'path': '/new', 'from': '/x'},
               {'op': 'unknown', 'path': '/new'},
               *({'op': op, 'path': '/x'} for op in ('add', 'replace', 'test')),
               {'op': 'remove', 'path': '/missing'},
               {'op': 'replace', 'path': '/missing', 'value': 0},
               {'op': 'add', 'path': '/missing/child', 'value': 0},
               {'op': 'add', 'path': '/x/child', 'value': 0}]
        for invalid in bad:
            document = {'x': None, 'keep': []}
            operations = [{'op': 'add', 'path': '/new', 'value': {'data': []}}, invalid]
            before = copy.deepcopy((document, operations))
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                apply_patch(document, operations)
            self.assertEqual((document, operations), before)

    def test_single_pass_and_iterator_failure(self):
        class Once:
            visits = 0
            def __iter__(self):
                self.visits += 1
                if self.visits != 1:
                    raise AssertionError('operations iterated more than once')
                yield {'op': 'add', 'path': '/created', 'value': [1]}
                yield {'op': 'test', 'path': '/created/0', 'value': 1.0}
                yield {'op': 'remove', 'path': '/created/0', 'ignored': True}
        operations = Once()
        self.assertEqual(apply_patch({}, operations), {'created': []})
        self.assertEqual(operations.visits, 1)
        sentinel = RuntimeError('operation source canary')
        document = {'keep': []}
        def interrupted():
            yield {'op': 'add', 'path': '/new', 'value': []}
            raise sentinel
        with self.assertRaises(RuntimeError) as caught:
            apply_patch(document, interrupted())
        self.assertIs(caught.exception, sentinel)
        self.assertEqual(document, {'keep': []})
        def stop_at_error():
            yield {'op': 'unknown', 'path': ''}
            raise AssertionError('read past rejected operation')
        with self.assertRaises(ValueError):
            apply_patch({}, stop_at_error())

    def test_cli_files_stdin_and_atomic_error_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            document = base / 'café config.json'
            patch = base / '雪 patch.json'
            document.write_text('{"items":[],"雪":true}', encoding='utf-8')
            patch.write_text('[{"op":"add","path":"/items/-","value":{"v":"café"}},'
                             '{"op":"test","path":"/items/0/v","value":"café"}]',
                             encoding='utf-8')
            original = {p: p.read_bytes() for p in (document, patch)}
            def cli(*args, input=None):
                return subprocess.run([sys.executable, '-B', '-m', 'docpatch', *map(str, args)],
                    cwd=ROOT, input=input, capture_output=True, encoding='utf-8', timeout=10)
            expected = {'items': [{'v': 'café'}], '雪': True}
            for result in (cli(document, patch), cli(document, '-', input=patch.read_text())):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertTrue(result.stdout.endswith('\n'))
                self.assertEqual(result.stderr, '')
            for raw in (b'{}', b'null', b'[{}]', b'[{"op":"test","path":"/missing","value":null}]',
                        b'[{"op":"add","path":"/x","value":NaN}]', b'not JSON', b'\xff'):
                bad = base / 'bad patch.json'; bad.write_bytes(raw)
                result = cli(document, bad)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
            for raw in ('[{"op":"add","path":"/new","value":1},'
                        '{"op":"test","path":"/missing","value":null}]',
                        '[{"op":"test","path":"/雪","value":true,"ignored":NaN}]',
                        '[{"op":"test","path":"/雪","value":true,"ignored":1e999}]'):
                result = cli(document, '-', input=raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
            empty_patch = base / 'empty patch.json'
            empty_patch.write_text('[]', encoding='utf-8')
            null_document = base / 'null document.json'
            null_document.write_text('null', encoding='utf-8')
            null_result = cli(null_document, empty_patch)
            self.assertEqual(null_result.returncode, 0, null_result.stderr)
            self.assertIsNone(json.loads(null_result.stdout))
            self.assertTrue(null_result.stdout.endswith('\n'))
            self.assertEqual(null_result.stderr, '')
            finite_duplicates = base / 'duplicate keys.json'
            finite_duplicates.write_text('{"x":1,"x":2}', encoding='utf-8')
            duplicated = cli(finite_duplicates, empty_patch)
            self.assertEqual(duplicated.returncode, 0, duplicated.stderr)
            self.assertEqual(json.loads(duplicated.stdout), {'x': 2})
            self.assertEqual(duplicated.stderr, '')
            for raw in (b'\xff', b'{', b'{"good":1,"bad":1e999}', b'{"bad":Infinity}',
                        b'{"x":1e999,"x":0}'):
                bad = base / 'bad document.json'; bad.write_bytes(raw)
                result = cli(bad, empty_patch)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
            bad.write_text('{"unused":1e999}', encoding='utf-8')
            discarded = cli(bad, '-', input='[{"op":"replace","path":"","value":0}]')
            self.assertEqual(discarded.returncode, 2)
            self.assertEqual(discarded.stdout, '')
            self.assertTrue(discarded.stderr.strip())
            for args in ((), (document,), (document, patch, patch),
                         (base / 'missing', patch), (document, base / 'missing')):
                result = cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.strip())
            for path, data in original.items():
                self.assertEqual(path.read_bytes(), data)


unittest.main(argv=['live-document-patch-verification'])
"""


_ASYNC_WINDOW_SOURCES: dict[str, str] = {
    "async_window/__init__.py": "from .pipeline import ordered_map\n",
    "async_window/pipeline.py": """async def ordered_map(function, iterable, *, limit=4):
    for item in list(iterable):
        yield await function(item)
""",
    "README.md": """# Bounded asynchronous ETL mapping

Repair async_window.pipeline.ordered_map(function, iterable, *, limit=4).
It returns an async iterator of awaited function(item) results in input order.
Use only the Python standard library. The synchronous iterable may be single-pass
and unbounded. Do not mutate/copy its items or close the caller-owned iterator.
The function may return any native awaitable, including a Future, and may raise
synchronously. Keep original result and exception objects, without wrapping them.

The stream is lazy: construction and closing an unstarted stream do not touch
the iterable or call the function. On the first pull, reject a limit that is not
a positive non-bool integer with ValueError before touching either input.
For valid input, acquire its iterator once. Submit up to limit items concurrently
before waiting for the first ordered result. The window includes completed but
unyielded items: never pull more than limit items ahead of yielded results.
Do not advance input or refill a vacancy while the consumer is paused at yield.
Refill when the next result is requested. Preserve input order even when work
finishes out of order; a later worker failure is raised at its ordered position.

If input iteration fails during a refill, propagate that original exception
immediately. On worker failure, input failure, caller cancellation, or explicit
aclose(), cancel and await all owned work before completing the transition.
Await asynchronous worker finalizers too. Peer cleanup errors must not replace
the original ordered worker/input error or caller cancellation. Explicit closure
discards remaining results/errors. Never cancel unrelated caller tasks. Consumers
that stop early use contextlib.aclosing or explicitly await stream.aclose().

Only async_window/pipeline.py may change. Keep this README and __init__.py intact.
Independent verification lives outside this source tree. This is a bounded
submission-window contract, not a claim about global process memory.
""",
}

_ASYNC_WINDOW_TASK = (
    "Repair the bounded asynchronous ETL mapper described in README.md. Preserve the public "
    "ordered_map API, ordered results/errors, lazy bounded submission window, native awaitables "
    "and deterministic awaited cleanup on cancellation/closure. Use only the Python standard "
    "library and edit async_window/pipeline.py only. Before editing, execute the exact supplied "
    "repository_tools.list Hashmarks command and read the frozen Skill reference. Independent "
    "verification runs after your coding Step."
)

_ASYNC_WINDOW_VERIFICATION = r"""import asyncio
from contextlib import aclosing
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve()))
from async_window import ordered_map

async def collect(function, source, *, limit=4):
    async with aclosing(ordered_map(function, source, limit=limit)) as stream:
        return [value async for value in stream]

class MapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_lazy_empty_and_invalid_limits(self):
        class Untouched:
            def __iter__(self):
                raise AssertionError('input touched')
        def forbidden(value):
            raise AssertionError('function called')
        stream = ordered_map(forbidden, Untouched())
        await stream.aclose()
        for limit in (True, False, 0, -1, 1.5, '2', None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                async with aclosing(ordered_map(forbidden, Untouched(), limit=limit)) as stream:
                    await anext(stream)
        self.assertEqual(await collect(forbidden, []), [])

    async def test_identity_and_single_pass_source(self):
        values = [object(), {'mutable': []}, None]
        original = tuple(values)
        class Source:
            iterations = 0
            closed = False
            def __iter__(self):
                self.iterations += 1
                if self.iterations != 1:
                    raise AssertionError('source restarted')
                return iter(values)
            def close(self):
                self.closed = True
        source = Source()
        async def identity(value):
            return value
        result = await collect(identity, source, limit=1)
        self.assertEqual(source.iterations, 1)
        self.assertFalse(source.closed)
        self.assertEqual(tuple(values), original)
        self.assertEqual(values[1], {'mutable': []})
        self.assertTrue(all(a is b for a, b in zip(result, original, strict=True)))

    async def test_concurrency_and_order(self):
        gates = [asyncio.Event() for _ in range(3)]
        ready = asyncio.Event()
        entered = set()
        active = 0
        peak = 0
        async def transform(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                entered.add(value)
                if {0, 1, 2} <= entered:
                    ready.set()
                if value < 3:
                    await gates[value].wait()
                return value * 10
            finally:
                active -= 1
        result = asyncio.create_task(collect(transform, range(9), limit=3))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            gates[2].set()
            gates[1].set()
            await asyncio.sleep(0)
            self.assertFalse(result.done())
            gates[0].set()
            self.assertEqual(await asyncio.wait_for(result, 5), list(range(0, 90, 10)))
            self.assertEqual(peak, 3)
            self.assertEqual(active, 0)
        finally:
            result.cancel()
            await asyncio.gather(result, return_exceptions=True)

    async def test_unbounded_source_window_and_explicit_close(self):
        pulled, entered, finalized = [], [], []
        blocked = asyncio.Event()
        def source():
            value = 0
            while True:
                pulled.append(value)
                yield value
                value += 1
        async def transform(value):
            entered.append(value)
            try:
                if value:
                    await blocked.wait()
                return value
            finally:
                await asyncio.sleep(0)
                finalized.append(value)
                if value:
                    raise RuntimeError('discarded explicit-close peer failure')
        # A finite tripwire avoids allowing an eager list() to exhaust host memory.
        def bounded_tripwire():
            for value in source():
                if value == 30:
                    raise AssertionError('unbounded prefetch')
                yield value
        stream = ordered_map(transform, bounded_tripwire(), limit=3)
        try:
            self.assertEqual(await asyncio.wait_for(anext(stream), 5), 0)
            await asyncio.sleep(0)
            self.assertEqual(pulled, [0, 1, 2])
            self.assertCountEqual(entered, [0, 1, 2])
            await stream.aclose()
            self.assertCountEqual(finalized, [0, 1, 2])
            self.assertEqual(pulled, [0, 1, 2])
        finally:
            await stream.aclose()

    async def test_ordered_failure_and_peer_cleanup(self):
        marker = LookupError('ordered failure')
        first = asyncio.Event()
        ready = asyncio.Event()
        entered, finalized = [], []
        async def transform(value):
            entered.append(value)
            if len(entered) == 3:
                ready.set()
            try:
                if value == 0:
                    await first.wait()
                    return 'first'
                if value == 1:
                    raise marker
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finalized.append(value)
                if value == 2:
                    raise RuntimeError('discarded peer cleanup failure')
        stream = ordered_map(transform, range(3), limit=3)
        request = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            self.assertFalse(request.done())
            first.set()
            self.assertEqual(await asyncio.wait_for(request, 5), 'first')
            with self.assertRaises(LookupError) as caught:
                await anext(stream)
            self.assertIs(caught.exception, marker)
            self.assertCountEqual(finalized, [0, 1, 2])
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            await stream.aclose()

    async def test_completed_results_still_occupy_submission_window(self):
        pulled = []
        def source():
            for value in range(12):
                pulled.append(value)
                yield value
        async def identity(value):
            return value
        async with aclosing(ordered_map(identity, source(), limit=3)) as stream:
            for expected in range(4):
                self.assertEqual(await anext(stream), expected)
                await asyncio.sleep(0)
                self.assertEqual(pulled, list(range(expected + 3)))

    async def test_explicit_close_leaves_caller_iterator_open(self):
        closed = []
        def source():
            try:
                yield from range(100)
            finally:
                closed.append(True)
        async def identity(value):
            return value
        caller_iterator = source()
        stream = ordered_map(identity, caller_iterator, limit=2)
        try:
            self.assertEqual(await anext(stream), 0)
            await stream.aclose()
            self.assertEqual(closed, [])
            self.assertEqual(next(caller_iterator), 2)
        finally:
            await stream.aclose()
            caller_iterator.close()
        self.assertEqual(closed, [True])

    async def test_native_awaitables_and_synchronous_failure(self):
        loop = asyncio.get_running_loop()
        marker = ArithmeticError('call-time failure')
        def native_future(value):
            if value == 2:
                raise marker
            result = loop.create_future()
            result.set_result(value)
            return result
        self.assertEqual(await collect(native_future, range(2)), [0, 1])
        async with aclosing(ordered_map(native_future, range(3), limit=2)) as stream:
            self.assertEqual(await anext(stream), 0)
            self.assertEqual(await anext(stream), 1)
            with self.assertRaises(ArithmeticError) as caught:
                await anext(stream)
            self.assertIs(caught.exception, marker)
        class Awaitable:
            def __init__(self, value):
                self.value = value
            def __await__(self):
                async def resolve():
                    return self.value
                return resolve().__await__()
        self.assertEqual(await collect(Awaitable, ['a', 'b']), ['a', 'b'])

    async def test_source_failure_cancels_and_awaits_owned_work(self):
        marker = OSError('source failed during refill')
        finalized = []
        class Source:
            closed = False
            position = 0
            iterations = 0
            def __iter__(self):
                self.iterations += 1
                if self.iterations != 1:
                    raise AssertionError('source restarted')
                return self
            def __next__(self):
                if self.position < 2:
                    value = self.position
                    self.position += 1
                    return value
                raise marker
            def close(self):
                self.closed = True
        async def transform(value):
            try:
                if value:
                    await asyncio.Event().wait()
                return value
            finally:
                await asyncio.sleep(0)
                finalized.append(value)
                if value:
                    raise RuntimeError('discarded input-failure peer error')
        source = Source()
        async with aclosing(ordered_map(transform, source, limit=2)) as stream:
            self.assertEqual(await asyncio.wait_for(anext(stream), 5), 0)
            await asyncio.sleep(0)
            with self.assertRaises(OSError) as caught:
                await anext(stream)
            self.assertIs(caught.exception, marker)
            self.assertCountEqual(finalized, [0, 1])
        self.assertFalse(source.closed)

    async def test_consumer_cancellation_awaits_workers_and_spares_other_tasks(self):
        ready = asyncio.Event()
        entered, finalized = [], []
        async def transform(value):
            entered.append(value)
            if len(entered) == 3:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finalized.append(value)
                if value == 2:
                    raise RuntimeError('discarded cancellation peer error')
        unrelated = asyncio.create_task(asyncio.Event().wait())
        stream = ordered_map(transform, range(10), limit=3)
        request = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertCountEqual(finalized, [0, 1, 2])
            self.assertFalse(unrelated.done())
        finally:
            request.cancel()
            unrelated.cancel()
            await asyncio.gather(request, unrelated, return_exceptions=True)
            await stream.aclose()

unittest.main(argv=['live-codex-async-window-verification'])
"""


_CIDR_POLICY_SOURCES: dict[str, str] = {
    "netpolicy/__init__.py": "from .policy import compile_policy\n",
    "netpolicy/policy.py": """import ipaddress


class Policy:
    def __init__(self, rules, default):
        self.rules = rules
        self.default = default

    def decide(self, address):
        parsed = ipaddress.ip_address(address)
        for rule in self.rules:
            if parsed in ipaddress.ip_network(rule['network'], strict=False):
                return {'action': rule['action'], 'network': rule['network']}
        return {'action': self.default, 'network': None}


def compile_policy(rules, *, default='deny'):
    return Policy(list(rules), default)
""",
    "netpolicy/__main__.py": """import json
from pathlib import Path
import sys

from .policy import compile_policy


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    policy = compile_policy(config['rules'], default=config.get('default', 'deny'))
    for line in Path(sys.argv[2]).read_text(encoding='utf-8').splitlines():
        print(json.dumps(policy.decide(line)))


if __name__ == '__main__':
    main()
""",
    "README.md": """# Offline IP access-policy lookup

Repair compile_policy(rules, *, default='deny') and the returned policy.decide(address).
Use only the Python standard library; ipaddress owns address/network parsing,
strict host-bit validation, network membership and canonical text formatting.
No DNS, networking or manual replacement parser is needed.

Rules are a one-pass synchronous iterable of dicts with required string fields
network and action. Actions/default are exactly 'allow' or 'deny'; reject all
other values with ValueError, validating default before consuming rules.
Require an explicit slash in network, reject scoped '%' forms, and use native
ipaddress.ip_network(network, strict=True) for the remaining accepted syntax.
Do not trim API strings. Ignore extra rule fields. Fully validate every rule,
including unreachable or redundant ones, while consuming the iterable once.
Identical canonical networks with equal actions are allowed; conflicting actions
for the same canonical network raise ValueError. Iterator exceptions propagate
unchanged, and validation stops at the first invalid rule.

decide accepts only an unscoped IP address string, using native ip_address.
Return a fresh dict {'action': ACTION, 'network': CANONICAL_NETWORK_OR_NONE}.
Choose the matching rule with the longest prefix, regardless of input order.
IPv4 and IPv6 stay separate; IPv4-mapped IPv6 remains IPv6. Without a matching
rule, return the configured default and network None. Empty policies are valid.
Never mutate caller input, retain mutable rule references or alias result dicts.
Later caller edits and failed compilations must not change a compiled policy.

CLI: python -m netpolicy POLICY QUERIES (exactly two arguments).
POLICY is a UTF-8 JSON object with required rules array and optional default.
Extra object fields are ignored. QUERIES is a UTF-8 text path, or '-' for stdin;
strip each query line and ignore blanks, preserving order and duplicate queries.
Emit exactly one JSON array plus newline. Each row contains address (native
canonical text), action and network. Validate all input and serialize the whole
array before stdout: any usage, I/O, decoding or validation failure exits 2,
prints a useful stderr diagnostic, and leaves stdout empty. Empty queries emit [].
Do not change input files. There is no global process-memory-bound claim.

Only netpolicy/policy.py and netpolicy/__main__.py may change. Keep __init__.py
and this README intact. Independent verification lives outside the source tree.
""",
}

_CIDR_POLICY_TASK = (
    "Repair the offline IP/CIDR access-policy lookup library and CLI described in README.md. "
    "Preserve compile_policy and decide, use native ipaddress authority, enforce longest-prefix "
    "and address-family semantics, validate the full one-pass rule source, detach policy state, "
    "and keep CLI errors atomic. Use only the Python standard library. Edit netpolicy/policy.py "
    "and netpolicy/__main__.py only. Honor the required string types for actions and defaults; "
    "mutable non-str wrappers such as collections.UserString must be rejected even when they "
    "compare equal to an allowed string. Before editing, execute the exact supplied "
    "repository_tools.list Hashmarks command and read the frozen Skill reference. "
    "Independent verification runs after your coding Step."
)

_CIDR_POLICY_VERIFICATION = r"""from collections import UserString
import copy
import ipaddress
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repo = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))
from netpolicy import compile_policy

class PolicyTests(unittest.TestCase):
    def test_longest_prefix_and_input_order(self):
        rules = [
            {'network': '0.0.0.0/0', 'action': 'deny'},
            {'network': '10.0.0.0/8', 'action': 'allow'},
            {'network': '10.20.0.0/16', 'action': 'deny'},
            {'network': '10.20.30.0/24', 'action': 'allow'},
            {'network': '10.20.30.7/32', 'action': 'deny'},
        ]
        expected = [
            ('192.0.2.1', 'deny', '0.0.0.0/0'),
            ('10.1.0.1', 'allow', '10.0.0.0/8'),
            ('10.20.31.1', 'deny', '10.20.0.0/16'),
            ('10.20.30.8', 'allow', '10.20.30.0/24'),
            ('10.20.30.7', 'deny', '10.20.30.7/32'),
        ]
        original = copy.deepcopy(rules)
        for source in (rules, list(reversed(rules)), rules[2:] + rules[:2]):
            policy = compile_policy(source)
            for address, action, network in expected:
                self.assertEqual(policy.decide(address), {'action': action, 'network': network})
        self.assertEqual(rules, original)

    def test_native_canonical_networks_and_duplicate_conflicts(self):
        policy = compile_policy([
            {'network': '192.0.2.0/255.255.255.0', 'action': 'allow'},
            {'network': '192.0.2.0/0.0.0.255', 'action': 'allow'},
            {'network': '192.0.2.0/24', 'action': 'allow', 'ignored': []},
            {'network': '2001:0db8:0000::/32', 'action': 'deny'},
            {'network': '2001:db8::/32', 'action': 'deny'},
        ])
        self.assertEqual(policy.decide('192.0.2.255'), {'action': 'allow', 'network': '192.0.2.0/24'})
        self.assertEqual(policy.decide('2001:db8::1'), {'action': 'deny', 'network': '2001:db8::/32'})
        for networks in [('192.0.2.0/24', '192.0.2.0/255.255.255.0'),
                         ('2001:0db8::/32', '2001:db8::/32')]:
            def source():
                for network, action in zip(networks, ['allow', 'deny']):
                    yield {'network': network, 'action': action}
                raise AssertionError('advanced past conflicting duplicate')
            with self.subTest(networks=networks), self.assertRaises(ValueError):
                compile_policy(source())

    def test_address_family_default_and_boundaries(self):
        rules = [
            {'network': '0.0.0.0/0', 'action': 'allow'},
            {'network': '::/0', 'action': 'deny'},
            {'network': '2001:db8::/32', 'action': 'allow'},
            {'network': '2001:db8::1/128', 'action': 'deny'},
        ]
        policy = compile_policy(rules)
        self.assertEqual(policy.decide('255.255.255.255'), {'action': 'allow', 'network': '0.0.0.0/0'})
        self.assertEqual(policy.decide('::ffff:192.0.2.1'), {'action': 'deny', 'network': '::/0'})
        self.assertEqual(policy.decide('2001:db8::1'), {'action': 'deny', 'network': '2001:db8::1/128'})
        self.assertEqual(policy.decide('2001:db8::2'), {'action': 'allow', 'network': '2001:db8::/32'})
        for default in ['allow', 'deny']:
            empty = compile_policy([], default=default)
            for address in ['0.0.0.0', '::', '192.0.2.1']:
                self.assertEqual(empty.decide(address), {'action': default, 'network': None})
        v4_only = compile_policy([{'network': '0.0.0.0/0', 'action': 'deny'}], default='allow')
        self.assertEqual(v4_only.decide('::ffff:192.0.2.1'), {'action': 'allow', 'network': None})

    def test_single_pass_detached_state_and_fresh_results(self):
        rules = [{'network': '192.0.2.0/24', 'action': 'allow'}]
        class Source:
            iterations = 0
            def __iter__(self):
                self.iterations += 1
                if self.iterations != 1:
                    raise AssertionError('source restarted')
                return iter(rules)
        source = Source()
        policy = compile_policy(source)
        self.assertEqual(source.iterations, 1)
        rules[0].update(network='198.51.100.0/24', action='deny')
        rules.append({'network': '0.0.0.0/0', 'action': 'allow'})
        result = policy.decide('192.0.2.1')
        self.assertEqual(result, {'action': 'allow', 'network': '192.0.2.0/24'})
        result['action'] = 'corrupt'
        self.assertEqual(policy.decide('192.0.2.1'), {'action': 'allow', 'network': '192.0.2.0/24'})
        with self.assertRaises(ValueError):
            compile_policy([{'network': '192.0.2.0/24', 'action': 'deny'},
                            {'network': 'bad', 'action': 'deny'}])
        self.assertEqual(policy.decide('192.0.2.1'), {'action': 'allow', 'network': '192.0.2.0/24'})
        self.assertEqual(policy.decide('198.51.100.1'), {'action': 'deny', 'network': None})

    def test_invalid_default_precedes_source_consumption(self):
        class Untouched:
            def __iter__(self):
                raise AssertionError('source touched before default validation')
        for default in [None, True, 1, [], {}, 'ALLOW', ' allow', '',
                        UserString('allow'), UserString('deny')]:
            with self.subTest(default=default), self.assertRaises(ValueError):
                compile_policy(Untouched(), default=default)

    def test_all_rules_validated_and_stop_at_first_invalid(self):
        invalid = [None, [], {}, {'network': '192.0.2.0/24'},
                   {'network': '192.0.2.0/24', 'action': []},
                   {'network': '192.0.2.0/24', 'action': UserString('allow')},
                   {'network': '192.0.2.0/24', 'action': UserString('deny')},
                   {'network': '192.0.2.0/24', 'action': 'ALLOW'},
                   {'network': 1, 'action': 'allow'},
                   {'network': '192.0.2.1/24', 'action': 'allow'},
                   {'network': '192.0.2.1', 'action': 'allow'},
                   {'network': 'fe80::%zone/64', 'action': 'allow'},
                   {'network': ' 192.0.2.0/24', 'action': 'allow'},
                   {'network': '::/129', 'action': 'deny'}]
        for bad in invalid:
            def source():
                yield {'network': '0.0.0.0/0', 'action': 'deny'}
                yield bad
                raise AssertionError('advanced past invalid rule')
            with self.subTest(rule=bad), self.assertRaises(ValueError):
                compile_policy(source())

    def test_iterator_failure_keeps_original_exception(self):
        marker = RuntimeError('rules producer failed late')
        def source():
            yield {'network': '0.0.0.0/0', 'action': 'allow'}
            raise marker
        with self.assertRaises(RuntimeError) as caught:
            compile_policy(source())
        self.assertIs(caught.exception, marker)

    def test_invalid_addresses(self):
        policy = compile_policy([])
        for address in [None, True, 1, [], {}, b'192.0.2.1', b'\xc0\x00\x02\x01', '', '192.0.2.1/32',
                        '192.0.2.256', ' 192.0.2.1', '192.0.2.1 ', '[::1]', 'fe80::1%zone']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                policy.decide(address)

    def run_cli(self, config, queries, *, stdin=False, raw_config=None, raw_queries=None,
                args=None, query_kind='file'):
        with tempfile.TemporaryDirectory(prefix='policy café ') as directory:
            policy_file = pathlib.Path(directory) / 'policy config.json'
            query_file = pathlib.Path(directory) / 'addresses α.txt'
            config_bytes = raw_config if raw_config is not None else json.dumps(config).encode()
            query_bytes = raw_queries if raw_queries is not None else queries.encode()
            policy_file.write_bytes(config_bytes)
            query_file.write_bytes(query_bytes)
            query_path = {'file': query_file, 'missing': pathlib.Path(directory) / 'missing.txt',
                          'directory': pathlib.Path(directory)}[query_kind]
            arguments = args if args is not None else [str(policy_file), '-' if stdin else str(query_path)]
            result = subprocess.run([sys.executable, '-B', '-m', 'netpolicy', *arguments],
                                    cwd=repo, input=query_bytes if stdin else None,
                                    capture_output=True, timeout=5)
            self.assertEqual(policy_file.read_bytes(), config_bytes)
            self.assertEqual(query_file.read_bytes(), query_bytes)
            return result

    def test_cli_files_and_stdin(self):
        config = {'rules': [{'network': '192.0.2.0/24', 'action': 'allow'},
                            {'network': '2001:db8::/32', 'action': 'allow'}],
                  'default': 'deny', 'note': 'nätverk'}
        addresses = ['192.0.2.7', '2001:0db8:0000::1', '198.51.100.2', '192.0.2.7']
        expected = [
            {'address': str(ipaddress.ip_address(address)), 'action': action, 'network': network}
            for address, action, network in zip(addresses,
                ['allow', 'allow', 'deny', 'allow'],
                ['192.0.2.0/24', '2001:db8::/32', None, '192.0.2.0/24'])]
        for stdin in [False, True]:
            result = self.run_cli(config, '\n  ' + '\r\n\n'.join(addresses) + '  \n', stdin=stdin)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
            self.assertTrue(result.stdout.endswith(b'\n'))
            self.assertEqual(json.loads(result.stdout), expected)
        result = self.run_cli({'rules': []}, '\n \n', stdin=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'[]\n', b''))

    def test_cli_late_errors_are_atomic(self):
        invalid_configs = [None, [], {}, {'rules': {}}, {'rules': [], 'default': []},
                           {'rules': [{'network': '0.0.0.0/0', 'action': 'allow'},
                                      {'network': '192.0.2.1/24', 'action': 'deny'}]}]
        for config in invalid_configs:
            with self.subTest(config=config):
                result = self.run_cli(config, '192.0.2.1\n')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr.strip())
        for stdin in [False, True]:
            result = self.run_cli({'rules': []}, '192.0.2.1\nnot-an-address\n', stdin=stdin)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())
        for keyword, invalid_bytes in [('raw_config', b'{'), ('raw_config', b'\xff'),
                                       ('raw_queries', b'192.0.2.1\n\xff')]:
            result = self.run_cli({'rules': []}, '', **{keyword: invalid_bytes})
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())

    def test_cli_usage_and_io_errors(self):
        for args in [[], ['one'], ['one', 'two', 'three'], ['missing-policy', 'missing-queries']]:
            result = self.run_cli({'rules': []}, '', args=args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())
        for query_kind in ['missing', 'directory']:
            result = self.run_cli({'rules': []}, '', query_kind=query_kind)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())

unittest.main(argv=['live-codex-cidr-policy-verification'])
"""


@pytest.mark.certification
@pytest.mark.parametrize(
    "scenario",
    [
        "ranges",
        "eventlog",
        "stocktake",
        "deploy-order",
        "ledger",
        "safe-zip",
        "http-node",
        "frames-node",
        "document-patch",
        "async-window",
        "cidr-policy",
    ],
)
def test_legacy_live_chatgpt_coding_uses_original_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    account_home = os.environ.get("OH_GOON_LIVE_CODEX_HOME")
    if not executable or not account_home:
        pytest.skip("Select both the installed Codex executable and a live ChatGPT account home")
    program = Path(executable).resolve(strict=True)
    home = Path(account_home).resolve(strict=True)
    assert home.is_dir()
    node_program: Path | None = None
    if scenario in {"http-node", "frames-node"}:
        node_executable = os.environ.get("OH_GOON_NATIVE_NODE")
        if not node_executable:
            pytest.skip("Select an installed public Node.js executable for the Node cases")
        node_program = Path(node_executable).resolve(strict=True)
        assert not node_program.is_relative_to(home)
    model = os.environ.get("OH_GOON_LIVE_CODEX_MODEL", "gpt-6-luna")
    repo = tmp_path / "repos" / ("bundle.workspace" if scenario == "safe-zip" else "app")
    repo.mkdir(parents=True)
    source_files = {
        "ranges": {"ranges.py": _BROKEN_SOURCE},
        "eventlog": _EVENTLOG_SOURCES,
        "stocktake": _STOCKTAKE_SOURCES,
        "deploy-order": _DEPLOY_ORDER_SOURCES,
        "ledger": _LEDGER_SOURCES,
        "safe-zip": _SAFE_ZIP_SOURCES,
        "http-node": _HTTP_NODE_SOURCES,
        "frames-node": _FRAMES_NODE_SOURCES,
        "document-patch": _DOCUMENT_PATCH_SOURCES,
        "async-window": _ASYNC_WINDOW_SOURCES,
        "cidr-policy": _CIDR_POLICY_SOURCES,
    }[scenario]
    for relative, text in source_files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    verifier = (
        tmp_path
        / "independent verification"
        / ("verify contract.mjs" if node_program else "verify contract.py")
    )
    verifier.parent.mkdir()
    verifier.write_text(
        {
            "ranges": _VERIFICATION,
            "eventlog": _EVENTLOG_VERIFICATION,
            "stocktake": _STOCKTAKE_VERIFICATION,
            "deploy-order": _DEPLOY_ORDER_VERIFICATION,
            "ledger": _LEDGER_VERIFICATION,
            "safe-zip": _SAFE_ZIP_VERIFICATION,
            "http-node": _HTTP_NODE_VERIFICATION,
            "frames-node": _FRAMES_NODE_VERIFICATION,
            "document-patch": _DOCUMENT_PATCH_VERIFICATION,
            "async-window": _ASYNC_WINDOW_VERIFICATION,
            "cidr-policy": _CIDR_POLICY_VERIFICATION,
        }[scenario],
        encoding="utf-8",
    )
    verifier_bytes = verifier.read_bytes()
    verification = (
        (str(node_program), str(verifier), str(repo))
        if node_program
        else (sys.executable, "-I", "-B", str(verifier), str(repo))
    )
    # The serial async fixture hits three independent five-second event guards.
    baseline_options = {"timeout": 30} if scenario == "async-window" else {}
    baseline = run_command(*verification, cwd=repo, **baseline_options)
    assert baseline.returncode == 1

    argv_verifier = verifier.parent / "verify arguments.py"
    quoted_arguments = (
        *((str(node_program),) if node_program else ()),
        "literal value with spaces",
        "https://example.invalid/tasks?cursor=alpha%20beta&tag=one",
    )
    syntax_check = (
        "sources = list(pathlib.Path(sys.argv[1]).rglob('*.mjs'))\n"
        "assert sources\n"
        "for source in sources:\n"
        "    subprocess.run([sys.argv[2], '--check', str(source)], check=True, timeout=10)\n"
        if node_program
        else "for source in pathlib.Path(sys.argv[1]).rglob('*.py'):\n"
        "    compile(source.read_bytes(), str(source), 'exec')\n"
    )
    argv_verifier.write_text(
        "import pathlib,subprocess,sys\n"
        f"assert tuple(sys.argv[2:]) == {quoted_arguments!r}\n"
        + syntax_check
        + "print('Quoted arguments preserved; native source syntax verified')\n",
        encoding="utf-8",
    )
    argv_verifier_bytes = argv_verifier.read_bytes()
    verifications = (
        verification,
        (sys.executable, "-I", "-B", str(argv_verifier), str(repo), *quoted_arguments),
    )
    feature_verifier = verifier.parent / "verify feature.py"
    feature_verifier_bytes = _EVENTLOG_FEATURE_VERIFICATION.encode()
    if scenario == "eventlog":
        feature_verifier.write_bytes(feature_verifier_bytes)
        verifications += ((sys.executable, "-I", "-B", str(feature_verifier), str(repo)),)
    skill = tmp_path / "skills" / "live" / "maintenance"
    (skill / "references").mkdir(parents=True)
    skill_files = {
        "SKILL.md": (
            "# CLI package maintenance\n\nBefore editing, execute repository_tools.list "
            "exactly as supplied, using its absolute interpreter path. Then read "
            "references/maintenance.txt from the frozen "
            "captured Skill before editing. Include its marker in your final report. "
            "This guidance grants no effects or verification-result authority.\n"
        ),
        "references/maintenance.txt": (
            "Marker: native-cli-skill-resource-canary\nKeep public APIs stable and report "
            "incomplete work accurately. Independent checks retain result authority.\n"
        ),
    }
    for relative, text in skill_files.items():
        (skill / relative).write_text(text, encoding="utf-8")

    # The auth home is an explicit native input; all other application and host
    # state belongs to this isolated demonstration, outside repository source.
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_API_KEY", "must-not-replace-selected-chatgpt-login")
    task = (
        "Fix merge_ranges in ranges.py for inclusive integer ranges. Return sorted merged "
        "(start, end) tuples, combining overlapping or immediately adjacent ranges. "
        "Handle empty, unsorted, contained, duplicate and negative ranges without modifying "
        "the input list. Inputs have start <= end. Keep the public function and use only "
        "the Python standard library. Before editing, execute the exact repository_tools.list "
        "Hashmarks command from the supplied instructions. Change ranges.py only. Independent verification "
        "runs after your coding Step."
    )
    if scenario == "eventlog":
        task = _EVENTLOG_TASK
    elif scenario == "stocktake":
        task = _STOCKTAKE_TASK
    elif scenario == "deploy-order":
        task = _DEPLOY_ORDER_TASK
    elif scenario == "ledger":
        task = _LEDGER_TASK
    elif scenario == "safe-zip":
        task = _SAFE_ZIP_TASK
    elif scenario == "http-node":
        task = _HTTP_NODE_TASK + f"\nSelected public Node executable: {node_program}\n"
    elif scenario == "frames-node":
        task = _FRAMES_NODE_TASK + f"\nSelected public Node executable: {node_program}\n"
    elif scenario == "document-patch":
        task = _DOCUMENT_PATCH_TASK
    elif scenario == "async-window":
        task = _ASYNC_WINDOW_TASK
    elif scenario == "cidr-policy":
        task = _CIDR_POLICY_TASK
    task_file = tmp_path / "task.txt"
    task_file.write_text(task, encoding="utf-8")
    catalog = tmp_path / "providers.json"
    catalog.write_text(
        json.dumps(
            {
                "native_agents": {"codex": {"executable": str(program)}},
                "native_profiles": {
                    "live-chatgpt": {
                        "backend": "local",
                        "allowed_agents": ["codex"],
                        "workspace_roots": [str(repo.parent)],
                        "timeout_seconds": 180,
                        "codex_login": {
                            "home": str(home),
                            "model": model,
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    native_facts: list[tuple[int, bool, bool]] = []
    native_diagnostics: dict[str, Any] = {}
    native_occurrences: list[str] = []
    candidate_identities: list[str] = []
    expected_source = {relative: text.encode() for relative, text in source_files.items()}
    expected_native_launches = 0
    prior_handoff: dict[str, Any] | None = None
    handoff_keys = ("fleet_run_id", "status", "outcome", "source_admissions", "recorded_steps")
    verification_launches: list[tuple[str, ...]] = []
    hashmarks_followup_succeeded = False
    hashmarks_search_succeeded = False
    hashmarks_combined_followup_succeeded = False
    skill_resource_reported = False

    def command_runner(*argv: str, **kwargs: Any) -> CommandResult:
        nonlocal hashmarks_followup_succeeded, hashmarks_search_succeeded
        nonlocal hashmarks_combined_followup_succeeded, skill_resource_reported
        if argv[0] == str(program):
            native_diagnostics["native_invocation_observed"] = True
            assert len(native_occurrences) == expected_native_launches, (
                "Each Run launches Codex once"
            )
            prompt = kwargs["input_data"]
            assert isinstance(prompt, bytes)
            payload = json.loads(prompt)
            assert payload["task"] == task
            assert payload["source_authority"]
            for relative, data in expected_source.items():
                assert (Path(payload["captured_repository"]) / relative).read_bytes() == data
            if prior_handoff is None:
                assert payload["context"] == {}
            else:
                context = payload["context"]
                assert context["authority"] == "UNTRUSTED_PRIOR_RUN_CONTEXT"
                assert context["permission_authority"].startswith("NONE")
                for key in handoff_keys:
                    assert context["prior_run_handoff"][key] == prior_handoff[key]
            assert payload["skill_instructions"] == skill_files["SKILL.md"]
            for relative, text in skill_files.items():
                assert (Path(payload["captured_skill"]) / relative).read_bytes() == text.encode()
            evidence = json.loads(payload["repository_evidence"])
            assert evidence["producer"] == "Hashmarks"
            assert (
                evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
            )
            assert evidence["capture_identity"] == payload["identity"]["candidate_identity"]
            assert evidence["capture_identity"] not in candidate_identities
            candidate_identities.append(evidence["capture_identity"])
            native_occurrences.append(payload["identity"]["reviewer"])
            settings: dict[str, Any] = {}
            for index, argument in enumerate(argv[:-1]):
                if argument == "-c":
                    settings.update(tomllib.loads(argv[index + 1]))
            assert Path(settings["model_instructions_file"]).read_bytes() == prompt
            assert settings["model"] == model
            assert settings["model_reasoning_effort"] == "low"
            assert "openai_base_url" not in settings and "chatgpt_base_url" not in settings
            native_environment_names = set(kwargs["env"])
            assert not {
                "CODEX_API_KEY",
                "OPENAI_API_KEY",
                "CODEX_ACCESS_TOKEN",
                "OPENAI_BASE_URL",
            }.intersection(native_environment_names)
            actual_home = kwargs["env"]["CODEX_HOME"]
            assert actual_home == str(home)
            result = run_command(*argv, **kwargs)
            native_diagnostics["native_returncode"] = result.returncode
            native_diagnostics["native_stderr_present"] = bool(result.stderr)
            native_diagnostics["native_stdout_present"] = bool(result.stdout)
            for relative, data in expected_source.items():
                assert (Path(payload["captured_repository"]) / relative).read_bytes() == data
            for relative, text in skill_files.items():
                assert (Path(payload["captured_skill"]) / relative).read_bytes() == text.encode()
            events = parse_native_agent_events("codex", result.stdout, returncode=result.returncode)
            skill_resource_reported = "native-cli-skill-resource-canary" in events.final_text
            item_facts: list[dict[str, str | int]] = []
            # Native typed edits expose ordering; shell writes are not classified here.
            native_edit_observed = False
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line).get("item")
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "file_change":
                    native_edit_observed = True
                if (
                    item_type == "command_execution"
                    and item.get("exit_code") == 0
                    and "oh_goon.integrations.hashmarks.agent_tools" in item.get("command", "")
                ):
                    list_followup_in_command = False
                    search_followup_in_command = False
                    for output_line in item.get("aggregated_output", "").splitlines():
                        try:
                            followup = json.loads(output_line)
                        except ValueError:
                            continue
                        if isinstance(followup, dict) and (
                            followup.get("producer") == "Hashmarks"
                            and followup.get("capture_identity")
                            == payload["identity"]["candidate_identity"]
                            and followup.get("producer_implementation_identity")
                            == require_loaded_hashmarks_runtime()
                        ):
                            if (
                                payload["repository_tools"]["list"] in item["command"]
                                and "paths" in followup
                            ):
                                assert set(followup["paths"]) == {
                                    "repo/" + relative for relative in expected_source
                                }
                                coverage = followup["discovery_coverage"]
                                assert coverage["admitted_member_count"] == len(expected_source)
                                assert coverage["repository_wide_completeness"] == "not-claimed"
                                assert (
                                    coverage["absence_outside_admitted_scope"] == "not-admissible"
                                )
                                if not native_edit_observed:
                                    hashmarks_followup_succeeded = True
                                    list_followup_in_command = True
                            elif scenario == "deploy-order" and "retrieval" in followup:
                                retrieval = followup["retrieval"]
                                assert retrieval["query"] == "order_services"
                                assert any(
                                    match["path"] == "deploy_order/graph.py"
                                    and match["name"] == "order_services"
                                    for match in retrieval["results"]
                                )
                                source = followup["source"]
                                assert source["authority"] == "repository-evidence-only"
                                assert source["execution_effect"] == "none"
                                assert any(
                                    match["path"] == "deploy_order/graph.py"
                                    and match["literal"] == "order_services"
                                    and match["member_revision"]
                                    and any(
                                        line["text"].strip() == "return sorted(dependencies)"
                                        for line in match["context_excerpt"]
                                    )
                                    for match in source["occurrences"]
                                )
                                if not native_edit_observed:
                                    hashmarks_search_succeeded = True
                                    search_followup_in_command = True
                    if list_followup_in_command and search_followup_in_command:
                        hashmarks_combined_followup_succeeded = True
                if len(item_facts) >= 32:
                    continue
                fact: dict[str, str | int] = {
                    "type": item_type
                    if item_type
                    in (
                        "agent_message",
                        "error",
                        "command_execution",
                        "mcp_tool_call",
                        "file_change",
                    )
                    else "other"
                }
                if item.get("status") in ("in_progress", "completed", "failed"):
                    fact["status"] = item["status"]
                if type(item.get("exit_code")) is int:
                    fact["exit_code"] = item["exit_code"]
                if item_type == "command_execution":
                    command = item.get("command", "")
                    output = item.get("aggregated_output", "")
                    fact["hashmarks_command"] = int(
                        "oh_goon.integrations.hashmarks.agent_tools" in command
                    )
                    fact["exact_hashmarks_list"] = int(
                        payload["repository_tools"]["list"] in command
                    )
                    fact["expected_interpreter"] = int(sys.executable in command)
                    fact["command_not_found"] = int("command not found" in output)
                    if fact["hashmarks_command"]:
                        fact["missing_query_file"] = int("No such file or directory" in output)
                        fact["denied_query_file"] = int("Permission denied" in output)
                        fact["database_unavailable"] = int("database is locked" in output)
                item_facts.append(fact)
            native_diagnostics.update(
                returncode=result.returncode,
                settled=events.settled,
                failed=events.failed,
                items=item_facts,
                observations=[item.summary for item in events.observations],
            )
            # Never include native output, account details or RPC payloads in a
            # failing assertion. Keep only bounded typed execution facts.
            native_facts.append((result.returncode, events.settled, events.failed))
            return result
        if argv in verifications:
            verification_launches.append(argv)
        return run_command(*argv, **kwargs)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_process_isolation.run_command", command_runner
    )

    def assert_source_edits(before: dict[str, bytes], allowed: set[str]) -> dict[str, bytes]:
        assert stat.S_ISDIR(repo.lstat().st_mode)
        actual_paths = {
            path.relative_to(repo).as_posix()
            for path in repo.rglob("*")
            if not {".git", "__pycache__"}.intersection(path.relative_to(repo).parts)
            and (path.is_symlink() or path.is_file())
        }
        assert actual_paths == set(before)
        for relative in before:
            path = repo / relative
            state = path.lstat()
            assert stat.S_ISREG(state.st_mode)
            assert state.st_nlink == 1
            assert all(not parent.is_symlink() for parent in path.parents if repo in parent.parents)
        after = {relative: (repo / relative).read_bytes() for relative in before}
        changed = {relative for relative in before if before[relative] != after[relative]}
        assert changed
        assert changed <= allowed
        return after

    arguments = [
        *([] if scenario == "stocktake" else ["--agent", "codex"]),
        "--profile",
        "live-chatgpt",
        "--config",
        str(catalog),
        "--workspace",
        str(repo),
        "--skill",
        str(skill),
        "--prompt-file",
        str(task_file),
        "--headless",
        "--json",
        *(value for command in verifications for value in ("--verify", shlex.join(command))),
    ]
    try:
        result = ai_main(arguments)
        captured = capsys.readouterr()
        assert result == (1 if scenario == "eventlog" else 0)
        try:
            detail = json.loads(captured.out)
        except ValueError:
            pytest.fail("The CLI must emit one valid Run detail", pytrace=False)
        assert native_facts == [(0, True, False)]
        assert hashmarks_followup_succeeded
        if scenario == "deploy-order":
            assert hashmarks_search_succeeded
            assert hashmarks_combined_followup_succeeded
        assert skill_resource_reported
        assert verification_launches == list(verifications)
        steps = detail["game_tape"]["repositories"][0]["steps"]
        assert detail["outcome"] == (
            FleetRunOutcome.ATTENTION_REQUIRED.value
            if scenario == "eventlog"
            else FleetRunOutcome.SUCCESS.value
        )
        assert [step["status"] for step in steps] == [GameTapeStatus.PASSED.value] * 3 + (
            [GameTapeStatus.FAILED.value] if scenario == "eventlog" else []
        )
        assert [tuple(step["command"]) for step in steps[1:]] == list(verifications)
        assert all(
            len(step["attempts"]) == 1
            and step["attempts"][0]["supervisor"]["final_process_group_quiescent"] is True
            for step in steps
        )
        native = steps[0]["native_agent_observations"][0]
        observation = (
            native["settled"],
            native["failed"],
            native["agent"],
            native["profile"],
            native["occurrence"],
        )
        assert observation == (True, False, "codex", "live-chatgpt", native_occurrences[0])
        assert verifier.read_bytes() == verifier_bytes
        assert argv_verifier.read_bytes() == argv_verifier_bytes
        for relative, text in skill_files.items():
            assert (skill / relative).read_bytes() == text.encode()
        first_source = assert_source_edits(
            expected_source,
            {
                "ranges": {"ranges.py"},
                "ledger": {"ledger/records.py", "ledger/database.py", "ledger/__main__.py"},
                "safe-zip": {"bundle_unpack/extract.py", "bundle_unpack/__main__.py"},
                "http-node": {"byte_asset/range.mjs", "byte_asset/handler.mjs"},
                "frames-node": {"telemetry/codec.mjs", "telemetry/cli.mjs"},
                "document-patch": {
                    "docpatch/pointer.py",
                    "docpatch/patch.py",
                    "docpatch/__main__.py",
                },
                "async-window": {"async_window/pipeline.py"},
                "cidr-policy": {"netpolicy/policy.py", "netpolicy/__main__.py"},
                "deploy-order": {"deploy_order/graph.py", "deploy_order/__main__.py"},
                "eventlog": {"eventlog/records.py", "eventlog/summary.py", "eventlog/__main__.py"},
                "stocktake": {
                    "stocktake/rows.py",
                    "stocktake/reconcile.py",
                    "stocktake/__main__.py",
                },
            }[scenario],
        )

        if scenario == "eventlog":
            assert feature_verifier.read_bytes() == feature_verifier_bytes
            first_run_id = detail["run"]["fleet_run_id"]
            assert detail["run"]["status"] == "COMPLETED"
            assert steps[-1]["exit_code"] == 1
            assert cli_main(["plan", "handoff", first_run_id, "--json"]) == 0
            prior_handoff = json.loads(capsys.readouterr().out)
            assert prior_handoff["outcome"] == FleetRunOutcome.ATTENTION_REQUIRED.value
            expected_source = first_source
            task = _EVENTLOG_FEATURE_TASK
            task_file.write_text(task, encoding="utf-8")
            expected_native_launches = 1
            hashmarks_followup_succeeded = False
            skill_resource_reported = False
            verification_launches.clear()
            result = ai_main([*arguments, "--handoff-from", first_run_id])
            captured = capsys.readouterr()
            assert result == 0
            detail = json.loads(captured.out)
            assert detail["run"]["fleet_run_id"] != first_run_id
            assert detail["outcome"] == FleetRunOutcome.SUCCESS.value
            assert native_facts == [(0, True, False)] * 2
            assert hashmarks_followup_succeeded and skill_resource_reported
            assert verification_launches == list(verifications)
            steps = detail["game_tape"]["repositories"][0]["steps"]
            assert [step["status"] for step in steps] == [GameTapeStatus.PASSED.value] * 4
            assert [tuple(step["command"]) for step in steps[1:]] == list(verifications)
            assert all(
                len(step["attempts"]) == 1
                and step["attempts"][0]["supervisor"]["final_process_group_quiescent"] is True
                for step in steps
            )
            assert steps[0]["native_agent_observations"][0]["occurrence"] == native_occurrences[1]
            assert_source_edits(expected_source, {"eventlog/summary.py", "eventlog/__main__.py"})
            assert feature_verifier.read_bytes() == feature_verifier_bytes
            assert cli_main(["plan", "handoff", first_run_id, "--json"]) == 0
            retained_handoff = json.loads(capsys.readouterr().out)
            for key in handoff_keys:
                assert retained_handoff[key] == prior_handoff[key]
        assert verifier.read_bytes() == verifier_bytes
        assert argv_verifier.read_bytes() == argv_verifier_bytes
        for relative, text in skill_files.items():
            assert (skill / relative).read_bytes() == text.encode()
    except AssertionError as error:
        native_diagnostics["verification_launches"] = len(verification_launches)
        for path, expected, key, invert in (
            (verifier, verifier_bytes, "independent_verifier_unchanged", False),
            (argv_verifier, argv_verifier_bytes, "argument_verifier_unchanged", False),
        ):
            same: bool | None = None
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is not None:
                try:
                    descriptor = os.open(path, os.O_RDONLY | nofollow | os.O_NONBLOCK)
                    with os.fdopen(descriptor, "rb") as stream:
                        state = os.fstat(stream.fileno())
                        if stat.S_ISREG(state.st_mode) and state.st_size <= 65536:
                            payload = stream.read(65537)
                            if len(payload) <= 65536:
                                same = payload == expected
                except OSError:
                    pass
            native_diagnostics[key] = (not same if invert else same) if same is not None else None
        error.add_note("Bounded native execution facts: " + json.dumps(native_diagnostics))
        raise
