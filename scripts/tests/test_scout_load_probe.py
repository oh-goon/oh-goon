from oh_goon.repository_tooling.scout_load_probe import run_probe


def test_scout_load_probe_exercises_projection_materialization_and_concurrent_cache() -> (
    None
):
    result = run_probe(
        repositories=20,
        steps_per_repository=2,
        concurrent_users=25,
        revision_cycles=3,
        changed_repositories_per_cycle=4,
    )

    assert result.repositories == 20
    assert result.observations == 40
    assert result.concurrent_users == 25
    assert result.overview_projection_ms >= 0
    assert result.findings_projection_ms >= 0
    assert result.store_materialization_ms >= 0
    assert result.revision_cycles == 3
    assert result.changed_repositories_per_cycle == 4
    assert result.revision_refresh_total_ms >= 0
    assert result.revision_refresh_p50_ms >= 0
    assert result.revision_refresh_p95_ms >= 0
    assert result.cache_misses == 1
    assert result.cache_hits >= 28
