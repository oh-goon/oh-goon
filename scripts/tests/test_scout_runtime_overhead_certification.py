from __future__ import annotations

from pathlib import Path

import pytest
from oh_goon.repository_tooling.process_metrics import read_linux_process_sample
from oh_goon.repository_tooling.scout_runtime_overhead_certification import (
    run_runtime_overhead_certification,
)

pytestmark = pytest.mark.certification

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    not Path("/proc/self/stat").is_file(), reason="Linux /proc is required"
)
def test_process_metric_reader_reports_current_process() -> None:
    sample = read_linux_process_sample(__import__("os").getpid())
    assert sample.cpu_seconds >= 0
    assert sample.rss_bytes > 0
    assert sample.peak_rss_bytes >= sample.rss_bytes


@pytest.mark.skipif(
    not Path("/proc/self/stat").is_file(), reason="Linux /proc is required"
)
def test_scout_runtime_overhead_probe_measures_real_server_process() -> None:
    result = run_runtime_overhead_certification(
        root=ROOT,
        repositories=20,
        steps_per_repository=2,
        concurrent_users=25,
    )
    assert result.observations == 40
    assert result.successful_requests == 25
    assert result.process_cpu_seconds >= 0
    assert result.rss_before_mib > 0
    assert result.rss_after_mib > 0
    assert result.peak_rss_mib >= result.rss_after_mib
    assert result.analytical_storage_mib > 0
    assert result.journal_lag_revisions == 0
    assert result.blocked_outbound_attempts == 0
