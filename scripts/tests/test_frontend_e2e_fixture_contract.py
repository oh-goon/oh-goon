from __future__ import annotations

from oh_goon.repository_tooling.architecture import frontend_base_contract


def test_current_e2e_fixture_sources_satisfy_generated_types_without_double_casts() -> None:
    assert frontend_base_contract.check_frontend_e2e_fixture_type_integrity() == []


def test_e2e_fixture_double_cast_is_rejected(tmp_path, monkeypatch) -> None:
    e2e = tmp_path / "e2e"
    e2e.mkdir()
    (e2e / "fixture.ts").write_text(
        "const value = {} as unknown as { readonly id: string };\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(frontend_base_contract, "FRONTEND_ROOT", tmp_path)
    violations = frontend_base_contract.check_frontend_e2e_fixture_type_integrity()
    assert len(violations) == 1
    assert "double-cast" in violations[0].message








def test_default_browser_fixture_publishes_included_fleet_authority_for_run_mutations() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    fleet = (root / "frontend/e2e/BrowserScaleFixture.ts").read_text(encoding="utf-8")

    assert "createIncludedFleet(repositories, NOW)" in client
    assert 'fleet: scenario === "empty" ? undefined : createIncludedFleet(repositories, NOW)' in client
    assert "return createFleetSnapshot(repositories, generatedAt, false);" in fleet
    assert "const excluded = includeScaleVariation && index % 97 === 0;" in fleet
    assert "const attention = includeScaleVariation && !excluded && index % 31 === 0;" in fleet






def test_browser_fixture_navigation_uses_declared_time_and_explicit_run_review_view() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    navigation = (root / "frontend/e2e/helpers/navigation.ts").read_text(encoding="utf-8")
    run_page = (root / "frontend/src/app/pages/RunsPage.tsx").read_text(encoding="utf-8")
    runs = (root / "frontend/e2e/runs.spec.ts").read_text(encoding="utf-8")

    assert 'page.clock.setFixedTime(new Date(FIXTURE_NOW))' in navigation
    assert 'const view = options.view ?? "flow";' in navigation
    assert '#runs?run=${encodeURIComponent(selectedRunId)}&view=${view}' in navigation
    assert 'patchRunReviewState(readRunReviewState(), { runId, view: "summary" })' in run_page
    assert 'toHaveURL(/view=summary/)' in runs
    assert 'getByRole("button", { name: "Overview" })' in runs


def test_initial_application_load_preserves_skip_link_as_first_keyboard_target() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    shell = (root / "frontend/src/app/AppShell.tsx").read_text(encoding="utf-8")
    accessibility = (root / "frontend/e2e/accessibility.spec.ts").read_text(encoding="utf-8")

    assert 'const previousRouteIdentity = useRef(`${page}:${locationIdentity}`);' in shell
    assert 'const routeChanged = previousRouteIdentity.current !== routeIdentity;' in shell
    assert 'if (routeChanged) document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });' in shell
    assert '<a className="skip-link" href="#main-content">' in shell
    assert 'await page.keyboard.press("Tab");' in accessibility
    assert 'await expect(skip).toBeFocused();' in accessibility


def test_dependency_fixture_materialization_preserves_population_and_repository_instance_authority() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    dependencies = (root / "frontend/e2e/dependencies.spec.ts").read_text(encoding="utf-8")
    fleet_scale = (root / "frontend/e2e/fleet-scale.spec.ts").read_text(encoding="utf-8")

    assert 'query_id: request.expected_population_id' in client
    assert 'repository_instance_ids: repositoryInstanceIds' in client
    assert 'this.dependencySelections.set(request.expected_population_id' in client
    assert 'dependency-selection-' not in client
    assert 'dependency-selection-1' not in dependencies
    assert 'dependency-selection-1' not in fleet_scale


def test_fleet_scale_browser_bounds_follow_production_page_owners() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    table = (root / "frontend/src/features/repositories/components/RepositoryTable.tsx").read_text(encoding="utf-8")
    shared = (root / "frontend/src/features/league/components/SharedPlanPanel.tsx").read_text(encoding="utf-8")
    fleet_scale = (root / "frontend/e2e/fleet-scale.spec.ts").read_text(encoding="utf-8")

    assert 'const PAGE_SIZE = 50;' in table
    assert 'const SHARED_PREVIEW_PAGE_SIZE = 100;' in shared
    assert 'inventory.locator("tbody tr")).toHaveCount(50)' in fleet_scale
    assert 'inventory.getByText("1–50 of 5000 repositories")' in fleet_scale
    assert 'details.advanced-disclosure > ul.compact-list > li' in fleet_scale
    assert 'repository: "repo-scale-00000", offset: "100"' in fleet_scale



def test_schedule_fixture_lock_preview_preserves_requested_plan_revision() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    hook = (root / "frontend/src/features/league/hooks/useSharedPlanLockPreview.ts").read_text(encoding="utf-8")

    assert 'if (value.plan_key !== planKey || value.sha256 !== planRevision)' in hook
    assert 'sha256: expectedRevision' in client
    assert 'digest: sha256:${expectedRevision}' in client


def test_run_history_filter_and_scout_browser_selectors_are_scoped_to_owned_surfaces() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    runs = (root / "frontend/e2e/runs.spec.ts").read_text(encoding="utf-8")
    scout = (root / "frontend/e2e/scout.spec.ts").read_text(encoding="utf-8")

    assert 'getByRole("group", { name: "Run filters", exact: true })' in runs
    assert 'filters.getByRole("button", { name: "Failed", exact: true })' in runs
    assert 'const evidenceDetails = page.locator("details", { hasText: "Evidence details" }).first();' in scout
    assert 'getByText("Comparable executions", { exact: true })' in scout
    assert 'new RegExp(`${LONG_GOON_NAME}-21`)' in scout
    assert 'getByText("1–1 of 5000")' in scout



def test_run_diff_browser_contract_opens_advanced_view_disclosure() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    runs = (root / "frontend/e2e/runs.spec.ts").read_text(encoding="utf-8")
    game_tape = (root / "frontend/src/features/game-tape/components/GameTape.tsx").read_text(encoding="utf-8")
    assert 'className="advanced-disclosure game-tape-more-views"' in game_tape
    assert 'locator("details.game-tape-more-views")' in runs
    assert 'advancedViews.getByRole("button", { name: "Run Diff" })' in runs
    assert 'restoredAdvancedViews.getByRole("button", { name: "Run Diff" })' in runs


def test_browser_live_output_fixture_honors_incremental_cursor_protocol() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    fixture = (root / "frontend/e2e/fixtures/gameTape.ts").read_text(encoding="utf-8")
    hook = (root / "frontend/src/features/game-tape/hooks/useGameTapeLiveOutput.ts").read_text(encoding="utf-8")

    assert 'override async gameTapeLiveOutput(runId: string, after = 0)' in client
    assert 'return fixtureLiveOutput(runId, after);' in client
    assert 'export function fixtureLiveOutput(runId: string, after = 0)' in fixture
    assert 'const chunks: GameTapeLiveOutputChunk[] = [' in fixture
    assert 'chunks: chunks.filter((chunk) => chunk.sequence > after)' in fixture
    request_block = hook.split("const request = useCallback", 1)[1].split(
        "const accept = useCallback", 1
    )[0]
    accept_block = hook.split("const accept = useCallback", 1)[1].split(
        "const { resource } = useReadResource", 1
    )[0]
    assert "const requestedAfter = owned.after;" in request_block
    assert "client.gameTapeLiveOutput(runId, requestedAfter" in request_block
    assert "owned.after =" not in request_block
    assert "owned.accumulated =" not in request_block
    assert "validateLiveOutputBatch(requestedAfter, owned.accumulated, incoming)" in accept_block
    assert "owned.accumulated = merged;" in accept_block
    assert "owned.after = incoming.state" in accept_block




def test_visibility_helper_runtime_filters_non_html_elements_before_inner_text_access() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    helper = (root / "frontend/e2e/helpers/visibility.ts").read_text(encoding="utf-8")
    visibility = (root / "frontend/e2e/content-visibility.spec.ts").read_text(encoding="utf-8")

    assert 'document.body.querySelectorAll("*")' in helper
    assert 'if (!(candidate instanceof HTMLElement)) continue;' in helper
    assert 'querySelectorAll<HTMLElement>("*")' not in helper.split("export async function expectNoSilentTextClipping", 1)[1].split("export async function expectNoDocumentHorizontalOverflow", 1)[0]
    assert 'silent clipping inspection tolerates visible SVG descendants' in visibility
    assert 'createElementNS("http://www.w3.org/2000/svg", "text")' in visibility








def test_dependency_fixture_population_identity_includes_every_materialization_filter_owner() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    contract = (root / "frontend/e2e/fixtures/BrowserFixtureApiClient.contract.test.ts").read_text(encoding="utf-8")

    assert 'coverage: "matching"' in client
    assert 'version_prefix: _versionPrefix ?? null' in client
    assert 'advisory_id: _advisoryId ?? null' in client
    assert 'materializes the exact advisory-filtered dependency population identity' in contract
    assert 'dependencyTargetSelection(page.population_id)' in contract


def test_dependency_selection_browser_flow_remains_plan_first_before_repository_checkboxes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    dependencies = (root / "frontend/e2e/dependencies.spec.ts").read_text(encoding="utf-8")
    plan = dependencies.index('savedPlanSelect(page).selectOption("python-dependency-inventory")')
    checkbox = dependencies.index('getByRole("checkbox", { name: /catalog-service/ })')
    assert plan < checkbox


def test_scale_browser_fixture_run_events_advance_beyond_durable_snapshot_authority() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    client = (root / "frontend/e2e/BrowserFixtureApiClient.ts").read_text(encoding="utf-8")
    contract = (root / "frontend/e2e/fixtures/BrowserFixtureApiClient.contract.test.ts").read_text(encoding="utf-8")

    assert 'journal_revision: this.scenario === "scale" ? 5002 : 204' in client
    assert 'journal_revision: this.scenario === "scale" ? 5003 : 205' in client
    assert 'keeps scale Run event causality newer than the durable scale snapshot' in contract
    assert 'expect(first.journal_revision).toBeGreaterThan(overview.journal_revision)' in contract
    assert 'expect(terminal.journal_revision).toBeGreaterThan(first.journal_revision)' in contract


def test_fleet_failure_investigation_enters_steps_before_using_flow_only_failure_action() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    fleet_scale = (root / "frontend/e2e/fleet-scale.spec.ts").read_text(encoding="utf-8")
    test_block = fleet_scale.split(
        'test("fleet-scale failure investigation preserves the Overview filter and lands on exact failed Step evidence"',
        1,
    )[1].split("\n});", 1)[0]

    steps = 'getByRole("button", { name: "Steps", exact: true }).click()'
    jump = 'getByRole("button", { name: "Jump to Failed Step", exact: true }).click()'
    assert steps in test_block
    assert jump in test_block
    assert test_block.index(steps) < test_block.index(jump)
