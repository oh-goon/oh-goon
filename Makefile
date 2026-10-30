.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

DEVELOPMENT_TEST_RUNNER = bash scripts/development_test.sh
DEVELOPMENT_CHECK_RUNNER = bash scripts/development_check.sh
APPLICATION_VERIFY_RUNNER = bash scripts/application_verify.sh
DEVELOPMENT_RESOURCE_RUNNER = bash scripts/development_test_resources.sh
DEPENDENCY_POLICY = uv run --no-project python backend/src/oh_goon/repository_tooling/dependency_resolution_bootstrap.py
PRE_COMMIT_REQUIREMENT = pre-commit>=4.0
DIAGNOSTIC_SURFACE ?= all
DIAGNOSTIC_BATCH ?= 1
DIAGNOSTIC_BATCH_LIMIT ?= 1
OH_GOON_DEBUG ?= 0
SOURCE_LAYOUT_DEBUG = $(if $(filter 1,$(OH_GOON_DEBUG)),--debug,)
CERTIFICATION_RUNNER = backend/.venv/bin/oh-goon-workflow
STATE_HOME ?= $(if $(XDG_STATE_HOME),$(XDG_STATE_HOME),$(HOME)/.local/state)
CACHE_HOME ?= $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)
TOOLING_TEST_RESUME_CONTEXT = --resume-context "node=$$(node --version 2>/dev/null || printf unavailable)" --resume-context "npm=$$(npm --version 2>/dev/null || printf unavailable)" --resume-context "uv=$$(uv --version 2>/dev/null || printf unavailable)" --resume-context "git=$$(git --version 2>/dev/null || printf unavailable)"
PYTEST_RESUME ?= --resume
# Empty preserves pytest's native testpaths; hosted CI partitions these with test-integration.
BACKEND_CHECK_TEST_ROOTS ?=
# Local checks stay serial; hosted jobs can use the existing bounded worker owner.
BACKEND_CHECK_TEST_WORKERS ?= 1
INTEGRATION_TEST_WORKERS ?= 1
OH_GOON_PYTEST_REPORT_PATH ?=
OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY ?=
PYTEST_REPORT_ARGUMENT = $(if $(OH_GOON_PYTEST_REPORT_PATH),--report-path "$(OH_GOON_PYTEST_REPORT_PATH)",)
# Canonical complete portable owners. Daily Actions consumes this same plan;
# membership inside each owner remains native pytest/npm authority.
RELEASE_CHECK_TARGETS = check-backend-quality check-backend-tests check-tooling-quality check-tooling-tests test-backend-integration test-cross-owner-integration test-python-projects frontend-check architecture check-generated release-locks-check
# Native pytest marker expressions: routine checks retain meaningful process/scale
# regressions; only exhaustive variants with bounded counterparts are deferred.
CHECK_PYTEST_MARKER ?= not certification and not slow
TOOLING_COMPLETE_TEST_MARKER ?= not certification
QUALIFY_EVIDENCE_DIR ?= ../OH_GOON_NATIVE_EVIDENCE
A1_KERNEL_EVIDENCE_DIR ?= ../OH_GOON_A1L_KERNEL_EVIDENCE
REPOSITORY_STATE_KEY = $(shell printf '%s' "$(CURDIR)" | sha256sum | cut -c1-16)
RUN_LOG_DIR ?= $(STATE_HOME)/oh-goon/run-logs/$(REPOSITORY_STATE_KEY)
PLAYWRIGHT_BROWSERS_PATH := $(CURDIR)/frontend/.oh-goon-playwright-browsers
COMMAND_BANNER = sh scripts/command_banner.sh
RUN_STEP = sh scripts/run_step.sh
INIT_DOCTOR = sh scripts/init_doctor.sh
DEVELOPMENT_TEST_FRONTDOOR = OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh test $(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_TEST_RUNNER)
CERTIFICATION_FRONTDOOR = OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh certify $(DEVELOPMENT_RESOURCE_RUNNER) $(CERTIFICATION_RUNNER)


.PHONY: a1-native-kernel-custody-test backend-python-capability-check python-static-sanity ruff-repair phase-evidence-convergence-check native-qualification-boundary-check candidate-convergence-preflight pre-commit pre-commit-acquire help init verify diagnostic-dependency-check backend-lock-check backend-package-build-check source-transport-clean sync venv-clean strict-executor-setup certification-preflight certification-database-preflight repository-understanding-test-batch repository-understanding-tests-batched certification-state-reset logs test-log certify-log diagnostics-log local-state-prelaunch-check local-state-reset storage-status storage-prune acceptance-release acceptance-browser-container acceptance-container acceptance-product acceptance-scale local-runtime-verification strict-local-runtime-acceptance release-locks-check release-version-set release-source-clean release-source-package-artifact frontend-install backend-dependencies-check frontend-functional-certification template-startability-certification frontend-lock start local-state-check local-state-path diagnostics-path local-state-reset dev release-accept docker-prepare docker-up docker-up-opensuse docker-down docker-accept docker-accept-opensuse lint i18n-check format format-check typecheck architecture goon-compile api-schema api-types goon-tool-authority contracts generate check-generated test test-python test-tooling test-tooling-complete test-frontend frontend-typecheck frontend-architecture frontend-build test-integration test-sqlite-real-usage test-real-lifecycle test-real-world test-real-world-parallel coverage-python coverage-report test-sqlite-coverage test-journal-coverage quality-check quality-raise check check-backend check-tooling check-frontend backend-check tooling-check frontend-check build-release assemble-release scout-load-probe scout-http-load-probe scout-network-certification scout-proxy-certification scout-overhead-certification scout-crash-restart-certification scout-failure-isolation-certification scout-corruption-recovery-certification scout-retention-checkpoint-certification diagnostic-system-check cli-install diagnostic-startup-check frontend-lock-check browser-setup browser-localhost-transport-preflight browser-preflight browser-harness-preflight browser-system-deps browser-runtime-preflight browser-targeted-certification browser-required-certification browser-test browser-check-system-chromium certification-explain repository-understanding-exam prepare-release certify test-execution-fast test-python-core-diagnostic test-python-dependency-complete test-python-complete verify-p0 verify-p1 verify-local init-doctor goon-static-admission backend-install theme-contrast test-python-profile tool-session-qualification tool-session-qualification-status recovery-status recovery-checkpoint recovery-resume qualify-native-wsl frontend-schedule-plan-input-regression

.PHONY: release-check release-check-plan check-backend-quality check-backend-tests check-tooling-quality check-tooling-tests test-backend-integration test-cross-owner-integration
.PHONY: test-python-projects

.DEFAULT:
	@case " $(MAKECMDGOALS) " in \
	  *" $@ "*) \
	    printf 'Unknown Oh-Goon make target: %s\n\n' "$@" >&2; \
	    $(MAKE) help >&2; \
	    exit 2 ;; \
	  *) printf 'Missing prerequisite with no Make rule: %s\n' "$@" >&2; exit 2 ;; \
	esac

help:
	@printf '%s\n' \
	  'oh-goon repository commands' \
	  '' \
	  'Canonical human workflow (locked):' \
	  '  make init' \
	  '  make test' \
	  '  make certify' \
	  '  make prepare-release                 Canonical release Plan frontdoor' \
	  '  All other targets are owner-local validation, diagnostic, internal/generated, or deprecated.' \
	  '  See docs/development/MAKE_TARGET_SURFACE.yml.' \
	  '' \
	  'Owner checks (non-canonical workflow):' \
	  '  make backend-check    Backend lint, format, types and behavior tests' \
	  '  make ruff-repair      Apply Ruff safe autofixes, then canonical Python formatting' \
	  '  make frontend-functional-certification  Current functional gate: typecheck, architecture, i18n, unit tests, build' \
	  '  make frontend-check   Strict later-release frontend gate: adds lint, coverage/quality checks' \
	  '  make tooling-check    Repository scripts + cross-owner tooling checks' \
	  '  make test-sqlite-coverage  SQLite authority + connection hygiene checks and coverage report' \
	  '  make test-journal-coverage Journal durability checks and coverage report' \
	  '  make coverage-report  Show the detailed Python coverage table from the latest collected data' \
	  '' \
	  'Golden change loops (stop on first failure):' \
	  '  backend internal: focused test -> backend-check' \
	  '  public API: focused API test -> backend-check -> generate -> check-generated -> test-integration -> frontend-check' \
	  '  frontend feature: focused colocated test -> frontend-check' \
	  '  template: focused template tests -> architecture -> backend-check' \
	  '  tooling: focused scripts test -> architecture -> tooling-check' \
	  '' \
	  'Completion evidence:' \
	  '  owner | focused validation | escalation gates | generated | architecture | unverified' \
	  '  timeout/interrupted/unavailable checks are unverified, never passed' \
	  '' \
	  'Capability-aware constrained development (diagnostic only):' \
	  '  OH_GOON_CONSTRAINED_HOST=1 make test  Probe and run already-ready backend/frontend owners; never acquire dependencies' \
	  '  OH_GOON_RUN_HOSTED_DIAGNOSTIC=1 make test  Explicitly request separate host-only sample alongside native checks' \
	  '  make DIAGNOSTIC_SURFACE=backend-unit DIAGNOSTIC_BATCH=1 DIAGNOSTIC_BATCH_LIMIT=4 test-python-core-diagnostic' \
	  '  make DIAGNOSTIC_EXTRA_MARKER="not <marker>" test-python-core-diagnostic  Narrow by one MARKER/not MARKER; boolean expressions rejected' \
	  '  compatible host cryptography is validated from pyproject intent + product-surface cryptography probe; a native uv cache miss is not package absence' \
	  '  host_dns marks tests that genuinely require external DNS/network; mocked/local HTTP tests stay runnable' \
	  '' \
	  'Contracts:' \
	  '  make generate         Capture OpenAPI and regenerate frontend contract' \
	  '  make check-generated  Verify generated contract without rewriting' \
	  '  make architecture     Run focused cross-owner boundary checks' \
	  '  make goon-compile     Statically validate every canonical Goon and execution authority contract' \
	  '  make repository-understanding-exam Validate the dependency/privacy-aware agent comprehension bank' \
	  '  make test-real-lifecycle Run the golden lifecycle/adversarial regression gate' \
	  '  make test-real-world   Run full-stack real user journey with process restart and durable reread' \
	  '  make test-real-world-parallel Require strict executor: cancel + failure repair + stale-preview fencing' \
	  '' \
	  'Acceptance (external evidence; never production dependencies):' \
	  '  make acceptance-release   Locked wheel -> clean install -> product journey -> restart' \
	  '  make acceptance-browser-container Same gate in the exact locked Playwright image' \
	  '  make acceptance-container Ubuntu + openSUSE -> product journey -> replace -> restore' \
	  '  make acceptance-product   Source runtime -> real Repo/Goon/Game Tape -> production browser' \
	  '  make acceptance-scale     Scout 5k/500 network/proxy/resources/crash/recovery/retention' \
	  '' \
	  'Diagnostics / local utilities (non-canonical workflow):' \
	  '  make diagnostic-system-check Read-only environment + lock diagnostics' \
	  '  make init             Native uv/npm developer materialization + tiny Oh-Goon smoke' \
	  '  make init-doctor      Diagnostic-only host storage probe; never admits/skips/repairs init' \
	  '  make storage-status   Show classified Oh-Goon state/cache usage using the repository-local CLI' \
	  '  make storage-prune    Safely prune owner-proven disposable state, including abandoned certification workspaces' \
	  '  make diagnostic-dependency-check Inspect the materialized backend dependency environment' \
	  '  make backend-lock-check Native uv pyproject.toml <-> uv.lock freshness + public publication check' \
	  '  make backend-package-build-check Native uv editable-package buildability check in an ephemeral venv' \
	  '  make sync             Intentionally resolve dependency changes and update committed native locks' \
	  '  make cli-install      Optional global oh-goon CLI installation' \
	  '  make frontend-install Materialize the locked npm graph; cache misses may use registry' \
	  '  make browser-setup     Install Playwright browser binaries only (no sudo)' \
	  '  make browser-localhost-transport-preflight Fail fast when Node localhost TCP transport is unhealthy' \
	  '  make browser-preflight Launch Chromium/Firefox/WebKit and report missing host deps' \
	  '  make browser-harness-preflight Load Playwright config/specs without running browser tests' \
	  '  make browser-runtime-preflight Require clean :5173/:8787 ownership before Gate D' \
	  '  make local-runtime-verification Start/probe/stop make dev for APP without strict executor authority' \
	  '  make strict-local-runtime-acceptance Production-shaped strict Fleet runtime/cancel/restart acceptance' \
	  '  make browser-targeted-certification Run the seven Gate D Chromium specs sequentially, one worker/process each' \
	  '  make browser-required-certification Run the complete required browser suite after preflights' \
	  '  make check            Broad development feedback with PASS/FAIL/BLOCKED summary' \
	  '    normal make test runs native uv/.venv backend and npm frontend tests; host-only sampling requires explicit opt-in' \
	  '  make local-state-check Inspect this checkout local-state schema without mutation' \
	  '  make local-state-prelaunch-check Prove SQLite write/lock/storage semantics before app launch' \
	  '  make local-state-reset Reset this checkout disposable SQLite state after an epoch mismatch' \
	  '  make browser-system-deps Explicitly install Playwright OS deps; may require sudo/root' \
	  '  make browser-test      Run the complete browser certification matrix' \
	  '  make sync             Intentional dependency authoring: committed native uv.lock + npm lock' \
	  '  make frontend-lock    Author only frontend/package-lock.json from frontend/package.json' \
	  '  make dev              Run backend + frontend development servers' \
	  '  make verify           Exhaustive whole-app diagnosis; continues independent failures; no APP PASS' \
	  '  make strict-executor-setup One-time WSL/systemd host preparation for explicit strict release/CI execution' \
	  '  make certify          Lean APP verification of the initialized checkout; no strict executor required' \
	  '  make test-log         Show the latest saved make test output' \
	  '  make certify-log      Show the latest saved make certify output' \
	  '  make diagnostics-log  Show passive application diagnostic evidence' \
	  '  make check            Broad development feedback; use docs/DEVELOPMENT.md for focused edits' \
	  '  make build-release    Build validated locked release artifacts' \
	  '  make assemble-release Assemble locked artifacts after caller-owned checks' \
	  '  make release-check    Run the complete portable regression suite' \
	  '  make docker-up        Run locked Ubuntu container locally' \
	  '  make docker-up-opensuse Run locked openSUSE container locally' \
	  '  make recovery-status RECOVERY_STORE=<durable> [RECOVERY_FORMAT=json|markdown] Verify and print committed source + continuation authority' \
	  '  make recovery-checkpoint RECOVERY_STORE=<durable> RECOVERY_CONTINUATION_INPUT=<json> ... Persist archive + continuation + receipts then CAS HEAD' \
	  '  make recovery-resume RECOVERY_STORE=<durable> RECOVERY_DESTINATION=<empty> Restore exact committed source and print continuation state'
	@printf '%s\n' \
	  '' \
	  'Portable source verification:' \
	  '  make verify-local      Prepare the exact bundled backend lock, then run P0 + P1' \
	  '  make verify-p0         Execution/security closeout without release/browser dependencies' \
  '  make qualify-native-wsl  Ordered WSL test ladder, stop on failure and preserve POST evidence (not APP PASS)' \
  '  make a1-native-kernel-custody-test  Explicit root-only Linux signer thread custody smoke (NOT attestation; refuses skips)'  \
	  '  make verify-p1         Real-repository authoring/input-resolution certification' \
	  '  see docs/LOCAL_VERIFICATION.md for strict release/browser follow-up'

diagnostic-dependency-check: backend-dependencies-check

backend-lock-check:
	@$(DEPENDENCY_POLICY) public-lock-check
	@uv lock --project backend --check

# OH_GOON_OPAQUE_TOOLS: node npm sh uv
init:
	@$(RUN_STEP) 'OH-GOON INIT [1/3]' 'Owner: native uv | locked backend materialization' -- uv sync --project backend --locked --group dev --group quality --group build
	@$(RUN_STEP) 'OH-GOON INIT [2/3]' 'Owner: native npm | committed frontend lock materialization' -- sh -c 'cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund'
	@$(RUN_STEP) 'OH-GOON INIT [3/3]' 'Owner: Oh-Goon | executable smoke' -- backend/.venv/bin/oh-goon --help
	@$(COMMAND_BANNER) 'OH-GOON INIT PASSED'

init-doctor:
	@$(INIT_DOCTOR)

storage-status:
	@backend/.venv/bin/oh-goon storage status --measure

storage-prune:
	@backend/.venv/bin/oh-goon storage prune --yes




frontend-lock-check:
	@cd frontend && npm ci --package-lock-only --ignore-scripts --dry-run --offline --no-audit --no-fund

diagnostic-startup-check: backend-dependencies-check
	@backend/.venv/bin/oh-goon-startup-environment check

release-locks-check: backend-lock-check frontend-lock-check

diagnostic-system-check: backend-lock-check frontend-lock-check
	@backend/.venv/bin/oh-goon-startup-environment doctor

cli-install: assemble-release
	uv tool install generated/release/dist/oh_goon-*-py3-none-any.whl

venv-clean:
	rm -rf backend/.venv

backend-package-build-check:
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT HUP INT TERM; \
		uv venv --project backend --no-python-downloads "$$tmp/venv" >/dev/null; \
		uv pip install --python "$$tmp/venv/bin/python" --no-deps --editable backend \
			--no-sources --no-python-downloads >/dev/null; \
		"$$tmp/venv/bin/oh-goon-source-layout" check >/dev/null

source-transport-clean:
	@bash scripts/source_transport_clean.sh

sync:
	@printf '%s\n' 'OH-GOON DEPENDENCY SYNC'
	@printf '%s\n' '[0/3] Source layout + native uv editable metadata admission before lock publication...'
	@$(MAKE) backend-package-build-check
	@printf '%s\n' '[1/3] Native uv authors backend/uv.lock from backend/pyproject.toml...'
	uv lock --project backend
	@$(DEPENDENCY_POLICY) public-lock-check
	@printf '%s\n' '[2/3] Native npm authors frontend/package-lock.json from frontend/package.json...'
	$(MAKE) frontend-lock
	@printf '%s\n' '[3/3] Native package managers materialize the authored locks...'
	$(MAKE) init
	@printf '%s\n' 'DEPENDENCY SYNC PASSED'
	@printf '%s\n' 'Review backend/pyproject.toml, backend/uv.lock, and frontend/package-lock.json.'

goon-static-admission:
	@backend/.venv/bin/oh-goon-goon-compile

certification-database-preflight:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'CERTIFICATION DATABASE PREFLIGHT' 'Owner: SQLite + Oh-Goon | current schema, DELETE journal, 600 fresh-connection stress reads, integrity + FK checks' -- backend/.venv/bin/oh-goon dev preflight-certification-database --state-root "$(STATE_HOME)"

certification-preflight: goon-static-admission certification-database-preflight
	@$(COMMAND_BANNER) 'CERTIFICATION PREFLIGHT PASSED' 'Static admission and control-state preflight passed; next: make certify'

backend-install:
	uv sync --project backend --locked --group dev --group quality --group build

# OH_GOON_OPAQUE_TOOLS: git ldd uv
verify-p0: python-static-sanity
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --marker "" --granularity tests --workers 1 --batch-size 64 --batch-timeout 180 \
		tests/unit/services/test_fleet_plan_input_resolution.py \
		tests/unit/services/test_execution_boundary_contract.py \
		tests/unit/services/test_execution_lock.py \
		tests/unit/services/test_fleet_plan_preparation_no_magic.py \
		tests/unit/services/test_fleet_plan_runtime.py \
		tests/integration/api/test_api_local_access.py \
		tests/unit/api/test_local_access_startup.py \
		tests/unit/runtime/test_local_host_isolation.py \
		tests/unit/runtime/test_run_admission_lock.py \
		tests/unit/runtime/test_readmission_lineage.py \
		tests/unit/integrations/test_fleet_plan_cicd_isolation.py \
		tests/unit/entrypoints/test_python_source_file_smoke.py
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-test-batches --qualified --show-output --marker "" --granularity tests --workers 1 --batch-size 64 --batch-timeout 180 \
		scripts/tests/test_simple_template_contract.py \
		scripts/tests/test_native_command.py
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-check-generated

# OH_GOON_OPAQUE_TOOLS: ldd sh true uv
template-startability-certification:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --marker "" --granularity tests --workers 1 --batch-size 8 --batch-timeout 180 tests/integration/services/test_catalog_template_startability.py

# OH_GOON_OPAQUE_TOOLS: bash git sh
verify-p1:
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-repository-authoring

verify-local: verify-p0 verify-p1

backend-python-capability-check:
	@backend/.venv/bin/oh-goon-project-dependency-capability

backend-dependencies-check:
	@backend/.venv/bin/oh-goon --help >/dev/null || { code=$$?; printf '%s\n' 'BACKEND ENVIRONMENT UNAVAILABLE' 'Native launch failed: backend/.venv/bin/oh-goon --help' 'Run: make init' >&2; exit $$code; }

# Ruff remains a visible static debt signal, but it is deliberately advisory: the
# repository carries known lint debt and qualification must not require debt cleanup.
# Ruff itself classifies safe fixability; normal qualification prints only findings that
# still require review. Hard locally-runnable syntax/import/global-name coverage lives
# in the per-source-file pytest closure. Qualification never mutates candidate source.
python-static-sanity:
	@printf '%s\n' '=== PYTHON RUFF ADVISORY ===' 'Owner: Ruff/Pyflakes | safe-fix noise summarized; review-required E9,F findings shown'
	@$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend; .venv/bin/ruff check --select E9,F --output-format json src tests ../scripts ../tests/integration | .venv/bin/oh-goon-ruff-advisory || printf "%s\n" "RUFF ADVISORY: structured report unavailable; locally runnable import/global-name tests remain authoritative." >&2; exit 0'

# Explicit mutating repair lane. Safe Ruff fixes and canonical formatting are applied only
# when the operator asks for them; test/verify/certify paths remain read-only.
ruff-repair:
	@printf '%s\n' '=== PYTHON RUFF REPAIR ===' 'Owner: Ruff | safe autofix then canonical formatting; unsafe fixes are never enabled'
	cd backend && .venv/bin/ruff check --fix-only --select E9,F src tests ../scripts ../tests/integration
	cd backend && .venv/bin/ruff format src tests ../scripts ../tests/integration
	@$(MAKE) --no-print-directory python-static-sanity

frontend-install:
	cd frontend && npm ci --prefer-offline --ignore-scripts --no-audit --no-fund


# OH_GOON_OPAQUE_TOOLS: node
frontend-functional-certification:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run verify:functional

browser-setup:
	cd frontend && PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" npm run browser:install

# OH_GOON_OPAQUE_TOOLS: node
browser-localhost-transport-preflight:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run browser:transport-preflight

# OH_GOON_OPAQUE_TOOLS: node
browser-preflight:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" npm run browser:preflight

# OH_GOON_OPAQUE_TOOLS: node
browser-harness-preflight:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run browser:harness-preflight

# OH_GOON_OPAQUE_TOOLS: node
browser-runtime-preflight:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" OH_GOON_BROWSER_REUSE_SERVER=0 npm run browser:runtime-preflight

# OH_GOON_OPAQUE_TOOLS: node npm
local-runtime-verification:
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-dev-runtime-acceptance verification

# OH_GOON_OPAQUE_TOOLS: bash kill mkdir node npm sh xargs
strict-local-runtime-acceptance:
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-dev-runtime-acceptance strict

browser-system-deps:
	@printf '%s\n' 'Installing Playwright operating-system dependencies; this operation may require sudo/root.'
	cd frontend && npm run browser:system-deps

# OH_GOON_OPAQUE_TOOLS: node
browser-targeted-certification: browser-runtime-preflight
	@set -eu; \
	for spec in shared-runs runs fleet-scale schedules accessibility content-visibility theme-integrity; do \
		printf '\n== Gate D Chromium: %s.spec.ts ==\n' "$$spec"; \
		$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" OH_GOON_BROWSER_REUSE_SERVER=0 npm run browser:test -- "e2e/$$spec.spec.ts" --project=chromium --workers=1 --max-failures=1; \
	done

certification-explain:
	@test -n "$(OH_GOON_RUN_ID)" || { echo "OH_GOON_RUN_ID=<fleet-run-id> is required" >&2; exit 2; }
	@$(CERTIFICATION_RUNNER) explain "$(OH_GOON_RUN_ID)"

certification-state-reset:
	@backend/.venv/bin/oh-goon dev reset-certification-state --state-root "$${XDG_STATE_HOME:-$$HOME/.local/state}"

prepare-release:
	@$(CERTIFICATION_RUNNER) release

# OH_GOON_OPAQUE_TOOLS: node
browser-required-certification: browser-runtime-preflight
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" OH_GOON_BROWSER_REUSE_SERVER=0 npm run browser:test

browser-test:
	$(MAKE) browser-required-certification

browser-check-system-chromium:
	@test -n "$(OH_GOON_CHROMIUM_EXECUTABLE)" || { echo "OH_GOON_CHROMIUM_EXECUTABLE is required" >&2; exit 2; }
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" OH_GOON_BROWSER_ENGINE=chromium OH_GOON_CHROMIUM_EXECUTABLE="$(OH_GOON_CHROMIUM_EXECUTABLE)" npm run browser:preflight
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh env PLAYWRIGHT_BROWSERS_PATH="$(PLAYWRIGHT_BROWSERS_PATH)" OH_GOON_CHROMIUM_EXECUTABLE="$(OH_GOON_CHROMIUM_EXECUTABLE)" npm run browser:test -- --project=chromium

theme-contrast:
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-theme-contrast


frontend-lock:
	cd frontend && npm install --package-lock-only --ignore-scripts --no-audit --no-fund
	cd frontend && npm ci --package-lock-only --ignore-scripts --dry-run --offline --no-audit --no-fund


start:
	cd backend && uv run --offline --frozen --no-sync oh-goon start

local-state-check:
	backend/.venv/bin/oh-goon-local-state check

local-state-prelaunch-check:
	backend/.venv/bin/oh-goon-local-state prelaunch-check

local-state-path:
	backend/.venv/bin/oh-goon-local-state path

diagnostics-path:
	backend/.venv/bin/oh-goon-local-state diagnostics-path

local-state-reset:
	backend/.venv/bin/oh-goon-local-state reset --yes

dev:
	bash scripts/dev.sh

lint:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh uv run --offline --frozen --no-sync --group quality ruff check src tests
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --group quality --project backend ruff check scripts tests/integration
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run lint

i18n-check:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run i18n:check

format:
	cd backend && uv run --offline --frozen --no-sync --group quality ruff format src tests
	uv run --offline --frozen --no-sync --group quality --project backend ruff format scripts tests/integration
	cd frontend && npm run lint:fix

format-check:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh uv run --offline --frozen --no-sync --group quality ruff format --check src tests
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --group quality --project backend ruff format --check scripts tests/integration

typecheck:
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --group quality --project backend mypy --config-file backend/pyproject.toml scripts/tests tests/integration


repository-understanding-exam:
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-repository-understanding-exam verify

pre-commit-acquire:
	uvx --from '$(PRE_COMMIT_REQUIREMENT)' pre-commit --version

pre-commit:
	uvx --offline --from '$(PRE_COMMIT_REQUIREMENT)' pre-commit run --all-files


phase-evidence-convergence-check:
	backend/.venv/bin/oh-goon-phase-evidence-convergence

native-qualification-boundary-check:
	backend/.venv/bin/oh-goon-native-qualification-boundary

candidate-convergence-preflight:
	$(MAKE) native-qualification-boundary-check
	$(MAKE) release-locks-check
	backend/.venv/bin/oh-goon-release-version check
	backend/.venv/bin/oh-goon-check-generated
	backend/.venv/bin/oh-goon-architecture
	backend/.venv/bin/oh-goon-repository-understanding-exam verify --quiet

architecture:
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-architecture

goon-compile:
	backend/.venv/bin/oh-goon-goon-compile

api-schema:
	backend/.venv/bin/oh-goon-export-openapi

api-types: api-schema
	backend/.venv/bin/oh-goon-generate-api-types

goon-tool-authority:
	backend/.venv/bin/oh-goon-generate-goon-tool-authority

contracts: api-types goon-tool-authority

generate: contracts

release-version-set:
	@test -n "$(OH_GOON_RELEASE_VERSION)" || { echo "OH_GOON_RELEASE_VERSION=x.y.z is required" >&2; exit 2; }
	backend/.venv/bin/oh-goon-release-version set "$(OH_GOON_RELEASE_VERSION)"

release-source-clean:
	backend/.venv/bin/oh-goon-source-normalize

release-source-package-artifact:
	uv run --project backend --locked --no-default-groups --group build oh-goon-package-source

check-generated:
	$(DEVELOPMENT_RESOURCE_RUNNER) backend/.venv/bin/oh-goon-check-generated

# Fast inner loop for the execution-integrity boundary. Keep this list deliberately
# focused: semantic lock/identity/admission/schedule contracts, not release or real
# child-process lifecycle tests.
test-execution-fast:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --marker "not slow and not process and not scale" --granularity tests --workers 1 --batch-size 64 --batch-timeout 180 \
		tests/unit/services/test_execution_boundary_contract.py \
		tests/unit/services/test_execution_lock.py \
		tests/unit/services/test_fleet_plan_preparation_no_magic.py \
		tests/unit/services/test_fleet_plan_runtime.py \
		tests/unit/services/test_shared_plan_schedules.py \
		tests/unit/services/test_fleet_target_scheduler.py \
		tests/unit/runtime/test_schedule_lifecycle.py \
		tests/unit/runtime/test_schedule_persistence.py \
		tests/unit/runtime/test_run_admission_lock.py \
		tests/unit/runtime/test_run_admission_singleflight.py \
		tests/unit/runtime/test_readmission_lineage.py \
		tests/unit/runtime/test_executor_readmission.py \
		tests/unit/runtime/test_fleet_worker_settlement.py \
		tests/unit/runtime/test_effect_boundary.py \
		tests/unit/runtime/test_database_path_ownership.py \
		tests/unit/runtime/test_preparation_cancellation.py \
		tests/unit/runtime/test_sqlite_prelaunch_stress.py \
		tests/unit/runtime/test_sqlite_prelaunch_writeability.py \
		tests/unit/runtime/test_certification_wait_sqlite_pressure.py \
		tests/unit/runtime/test_sqlite_adversarial_identity.py \
		tests/unit/runtime/test_sqlite_fault_matrix.py

# Explicit hosted-development sampling, not part of the normal native make test path.
# This shell helper never resolves/installs dependencies, mutates project dependency
# state, or becomes certification authority. By default it samples one deterministic
# batch from backend unit, backend integration, and repository-tooling surfaces.
# DIAGNOSTIC_SURFACE / DIAGNOSTIC_BATCH / DIAGNOSTIC_BATCH_LIMIT can widen a hosted investigation;
# complete backend/generated ownership remains native uv + backend/.venv.
test-python-core-diagnostic:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'PRE-DEPENDENCY TEST DIAGNOSTIC' 'Owner: host pytest | bounded dependency-light diagnostic; never certification authority' -- env OH_GOON_HOSTED_DIAGNOSTIC_RECEIPT="$(RUN_LOG_DIR)/test/hosted-diagnostic-receipt.json" OH_GOON_HOSTED_DIAGNOSTIC_STATUS="$(RUN_LOG_DIR)/test/hosted-diagnostic-exit.status" DIAGNOSTIC_SURFACE="$(DIAGNOSTIC_SURFACE)" DIAGNOSTIC_BATCH="$(DIAGNOSTIC_BATCH)" DIAGNOSTIC_BATCH_LIMIT="$(DIAGNOSTIC_BATCH_LIMIT)" bash scripts/backend_core_test_diagnostic.sh

# The leaf transport compatibility surface intentionally waits until backend dependencies
# are complete. Missing httpx2 must block this late job, never the unrelated core suite.
# Process lifecycle proofs use their own serialized lane and native timeout budget.
test-python-dependency-complete:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND DEPENDENCY-COMPLETE UNIT TESTS' 'Owner: Oh-Goon pytest batches | complete non-process qualification' -- backend/.venv/bin/oh-goon-test-batches backend/tests/dependency_complete --granularity files --batch-size 2 --workers 1 --batch-timeout 60 --suite-timeout 120 --marker 'not process'
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND DEPENDENCY-COMPLETE PROCESS TESTS' 'Owner: Oh-Goon pytest batches | semantic process supervision is serialized' -- backend/.venv/bin/oh-goon-test-batches backend/tests/dependency_complete --granularity files --batch-size 1 --workers 1 --batch-timeout 180 --suite-timeout 600 --marker 'process'

# Profiling is diagnostic, never part of the normal gate. It exposes accidental
# suite growth without making wall-clock timing a correctness assertion.
test-python-profile:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --pytest-config pyproject.toml --marker "" --granularity tests --workers 1 --batch-size 64 --batch-timeout 180 tests/unit tests/dependency_complete --show-output --pytest-arg=--durations=40 --pytest-arg=--durations-min=0.05

# Fast backend repair/development gate. Selection is semantic: real-process, scale,
# certification and explicitly slow tests stay outside the inner loop. Scheduling itself
# is generic; timeout refinement owns any coarse shard that proves too large at runtime.
# OH_GOON_OPAQUE_TOOLS: git ldd node npm true uv
test-python: python-static-sanity
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND FAST UNIT TESTS' 'Owner: Oh-Goon pytest batches | semantic selection + adaptive timeout refinement' -- backend/.venv/bin/oh-goon-test-batches backend/tests/unit --granularity files --batch-size 4 --workers 4 --batch-timeout 120 --suite-timeout 1200 $(PYTEST_RESUME) --resume-key backend-fast-unit-files --marker 'not slow and not process and not scale and not certification'
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND DEPENDENCY-COMPLETE UNIT TESTS' 'Owner: Oh-Goon pytest batches | 60s hard timeout' -- backend/.venv/bin/oh-goon-test-batches backend/tests/dependency_complete --granularity files --batch-size 2 --workers 1 --batch-timeout 60 --suite-timeout 120 --marker 'not slow and not process and not scale and not certification'

# Complete backend qualification uses one generic non-process lane. Process-supervision
# tests own the explicit ``process`` semantic marker and are serialized as a distinct
# execution class; filenames never select batching, workers, or timeout policy.
# OH_GOON_OPAQUE_TOOLS: git ldd node npm true uv
test-python-complete: python-static-sanity
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND COMPLETE UNIT TESTS' 'Owner: Oh-Goon pytest batches | complete non-process qualification' -- backend/.venv/bin/oh-goon-test-batches backend/tests/unit --granularity files --batch-size 4 --workers 2 --batch-timeout 180 --suite-timeout 1200 --environment-retries 1 --marker 'not process'
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND COMPLETE PROCESS TESTS' 'Owner: Oh-Goon pytest batches | semantic process supervision is serialized' -- backend/.venv/bin/oh-goon-test-batches backend/tests/unit --granularity files --batch-size 1 --workers 1 --batch-timeout 180 --suite-timeout 600 --environment-retries 1 --marker 'process'
	@$(MAKE) test-python-dependency-complete

repository-understanding-test-batch:
	@test -n "$(BATCH)" || { echo "BATCH=1..7 is required" >&2; exit 2; }
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'REPOSITORY UNDERSTANDING TEST BATCH' 'Owner: Oh-Goon | one bounded agent-safe pytest batch' -- backend/.venv/bin/oh-goon-test-batches scripts/tests/test_repository_understanding_certification.py --granularity tests --batch-size 4 --workers 1 --batch-timeout 90 $(PYTEST_RESUME) --resume-key repository-understanding-batched --batch "$(BATCH)"

repository-understanding-tests-batched:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'REPOSITORY UNDERSTANDING TEST BATCHES' 'Owner: Oh-Goon | deterministic node-level pytest timeout firewall' -- backend/.venv/bin/oh-goon-test-batches scripts/tests/test_repository_understanding_certification.py --granularity tests --batch-size 4 --workers 1 --batch-timeout 90 $(PYTEST_RESUME) --resume-key repository-understanding-batched

# Development/repair tooling gate. Selection is semantic; the generic batch runner owns
# concurrency, timeout enforcement, and deterministic timeout refinement. A test filename
# never grants a different execution profile.
test-tooling:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'TOOLING FAST TESTS' 'Owner: Oh-Goon pytest batches | semantic selection + adaptive timeout refinement' -- backend/.venv/bin/oh-goon-test-batches scripts/tests --granularity files --batch-size 4 --workers 2 --batch-timeout 90 --suite-timeout 1200 $(PYTEST_RESUME) $(TOOLING_TEST_RESUME_CONTEXT) --resume-key tooling-fast-files --marker 'not certification and not process and not slow'

# A1k: optional read-only disposable-kernel custody proof. This is deliberately
# independent of the original M209 diagnostic sequence: normal WSL users are
# not root, so silently skipped process tests cannot certify that sequence.
# The preflight fails before running pytest if the host cannot exercise both
# the genuine non-root signer and root-sibling counterexample.
a1-native-kernel-custody-test:
	@test "$$(id -u)" = 0 && test "$$(uname -s)" = Linux && test "$$(uname -m)" = x86_64 || { echo 'A1 KERNEL CUSTODY BLOCKED: Linux x86_64 root required' >&2; exit 2; }
	@command -v setpriv >/dev/null && command -v uv >/dev/null && test -d /opt && test -w /opt && test -x backend/.venv/bin/oh-goon-qualification-exec || { echo 'A1 KERNEL CUSTODY BLOCKED: root-owned /opt, setpriv, uv and locked backend required' >&2; exit 2; }
	@$(DEVELOPMENT_RESOURCE_RUNNER) bash scripts/a1_kernel_custody.sh "$(A1_KERNEL_EVIDENCE_DIR)" scripts/tests/test_a1h_real_linux_process_custody.py

# Complete repository-tooling qualification uses the same generic scheduling authority.
# Certification-marked recursive tests remain owned by APP certification.
# OH_GOON_OPAQUE_TOOLS: bash dd git node uv
test-tooling-complete:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'TOOLING COMPLETE TESTS' 'Owner: Oh-Goon pytest batches | complete non-certification qualification' -- backend/.venv/bin/oh-goon-test-batches scripts/tests --granularity files --batch-size 4 --workers 2 --batch-timeout 180 --suite-timeout 1800 --marker "$(TOOLING_COMPLETE_TEST_MARKER)" $(PYTEST_REPORT_ARGUMENT)

test-frontend:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run test

frontend-typecheck:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run typecheck

frontend-architecture:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run architecture

frontend-build:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run build

test-integration: test-backend-integration test-cross-owner-integration

test-backend-integration:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'BACKEND INTEGRATION TESTS' 'Owner: qualified native pytest batches | complete ordinary selection' -- sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml tests/integration --granularity tests --workers $(INTEGRATION_TEST_WORKERS) --batch-size 8 --batch-timeout 180 --marker "$(CHECK_PYTEST_MARKER)" $(PYTEST_REPORT_ARGUMENT)

test-cross-owner-integration:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'CROSS-OWNER INTEGRATION TESTS' 'Owner: qualified native pytest batches' -- backend/.venv/bin/oh-goon-test-batches --qualified --show-output tests/integration --granularity tests --workers $(INTEGRATION_TEST_WORKERS) --batch-size 8 --batch-timeout 180 --marker "$(CHECK_PYTEST_MARKER)" $(PYTEST_REPORT_ARGUMENT)

test-python-projects:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'STANDALONE PYTHON PROJECT TESTS' 'Owner: native uv | Iris and orders application tests' -- uv run --offline --frozen --no-sync --project backend oh-goon-python-project-checks $(if $(OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY),--report-directory "$(OH_GOON_PYTHON_PROJECT_REPORT_DIRECTORY)" --continue-independent,)

# Compact production-shaped SQLite lifetime gate. This deliberately crosses the
# worker/wait/restart boundary and complements component/fault-matrix coverage.
test-sqlite-real-usage:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'SQLITE REAL-USAGE QA' 'Owner: Oh-Goon | active wait -> terminal handoff -> restart -> durable reread' -- backend/.venv/bin/oh-goon-test-batches \
		backend/tests/integration/api/test_sqlite_real_usage_lifecycle.py \
		backend/tests/unit/runtime/test_terminal_completion_handoff.py \
		backend/tests/unit/runtime/test_certification_wait_sqlite_pressure.py \
		backend/tests/unit/runtime/test_execution_connection_scope.py \
		--granularity files --batch-size 1 --workers 1 --batch-timeout 90 --suite-timeout 300 --no-timeout-bisection --marker ''

# Golden production-shaped journeys. This gate intentionally crosses owner boundaries
# and includes adversarial/mutation proofs; raw unit-test count is not its success metric.
test-real-lifecycle:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'REAL LIFECYCLE QA' 'Owner: qualification execution -> pytest | public entrypoints -> authority -> durability -> teardown/reload -> observed state' -- backend/.venv/bin/oh-goon-test-batches --qualified --show-output --marker "" --granularity tests --workers 1 --batch-size 8 --batch-timeout 180 \
		scripts/tests/test_real_lifecycle_qa_contract.py \
		scripts/tests/test_init_hostile_clean_extraction.py \
		scripts/tests/test_native_real_lifecycle_artifact_contract.py \
		scripts/tests/test_run_logging.py::test_logged_runner_suppresses_spinner_frames_when_output_is_captured \
		scripts/tests/test_run_logging.py::test_logged_runner_uses_one_live_spinner_line_on_a_real_terminal \
		backend/tests/integration/api/test_real_lifecycle_golden.py \
		backend/tests/integration/api/test_api_recovery.py \
		backend/tests/integration/api/test_api_run_lifecycle.py::test_execution_observations_api_projects_existing_run_game_tape_facts \
		backend/tests/unit/runtime/test_executor_readmission.py::test_readmission_creates_new_intent_and_preserves_source_run \
		backend/tests/unit/runtime/test_fleet_worker_settlement.py::test_fleet_worker_fails_closed_if_current_capability_no_longer_matches_admission \
		backend/tests/unit/runtime/test_fleet_worker_settlement.py::test_fleet_terminal_settlement_replays_canonical_terminal_state_after_ambiguous_response \
		backend/tests/unit/runtime/test_fleet_worker_settlement.py::test_fleet_cancel_replays_canonical_request_after_ambiguous_commit_response \
		backend/tests/unit/runtime/test_recovery_reconciliation.py::test_concurrent_reconciliation_retries_replay_one_canonical_result \
		backend/tests/unit/runtime/test_recovery_reconciliation.py::test_reconciliation_compare_and_set_rejects_stale_writer \
		backend/tests/unit/services/test_shared_run_scope.py::test_shared_plan_run_still_preserves_explicit_multi_repository_scope \
		backend/tests/unit/services/test_shared_plan_service.py::test_shared_plan_run_rejects_exact_source_revision_change_even_before_execution \
		backend/tests/unit/core/test_game_tape_projection.py::test_game_tape_projects_plan_order_timing_outcomes_and_safe_provenance \
		backend/tests/unit/core/test_game_tape_projection.py::test_game_tape_lifecycle_rejects_unprojectable_terminal_facts \
		backend/tests/unit/integrations/test_fleet_plan_tool_authority.py::test_direct_attempt_tool_evidence_survives_post_launch_executable_mutation \
		backend/tests/unit/integrations/test_fleet_plan_runtime_boundaries.py::test_strict_preparation_cancellation_callback_does_not_escape_into_execution \
		backend/tests/unit/integrations/test_fleet_plan_runtime_boundaries.py::test_callback_lifetime_regression_proof_goes_red_when_boundary_is_mutated \
		backend/tests/unit/integrations/test_fleet_plan_cache_isolation.py::test_retained_cache_object_cannot_cross_repository_ownership_boundary \
		backend/tests/integration/api/test_sqlite_real_usage_lifecycle.py \
		backend/tests/unit/runtime/test_terminal_completion_handoff.py::test_local_wait_reconstructs_terminal_truth_from_fresh_sqlite_after_worker_retires \
		backend/tests/unit/runtime/test_preparation_cancellation.py::test_persistent_preparation_observer_survives_sustained_concurrent_sqlite_writer \
		backend/tests/unit/runtime/test_sqlite_fault_matrix.py::test_observer_cache_release_detects_in_place_header_corruption_without_reopen \
		backend/tests/unit/runtime/test_local_state_maintenance.py::test_source_local_state_reset_entrypoint_accepts_make_confirmation \
		scripts/tests/test_repository_understanding_certification.py::test_perfect_semantic_observations_produce_high_confidence_certificate \
		scripts/tests/test_repository_understanding_certification.py::test_critical_claim_misunderstanding_vetoes_qualification_even_with_high_average

# Expensive production-shaped user journey. Unlike test-real-lifecycle, this launches
# the actual backend + Vite dev processes, drives HTTP through the browser-facing proxy,
# executes a real repository Goon, kills the process group, restarts it on the same
# durable state, and proves old/new user-visible evidence.
test-real-world:
	@$(RUN_STEP) 'REAL-WORLD USAGE QA' 'Owner: Oh-Goon | make dev -> HTTP auth/proxy -> physical cancel/busy isolation -> hard restart -> durable reread' -- $(MAKE) strict-local-runtime-acceptance

# Strict Shared Plan parallelism is an executor-owned native proof. Never fabricate
# a non-strict executor just to make the test green. Operators run this with the
# same validated OH_GOON_EXECUTORS_FILE used for strict CI/CD execution.
test-real-world-parallel:
	@test -n "$$OH_GOON_EXECUTORS_FILE" || { echo "test-real-world-parallel requires OH_GOON_EXECUTORS_FILE pointing to a validated strict executor inventory" >&2; exit 2; }
	@$(RUN_STEP) 'REAL-WORLD PARALLEL SHARED PLAN QA' 'Owner: Oh-Goon | strict executor -> physical cancel -> sibling failure repair -> stale preview rejected pre-effect -> refreshed success' -- $(MAKE) strict-local-runtime-acceptance


# Resume-safe development qualification for outer tool/session limits. Each invocation
# executes one exact-identity unit; rerun until COMPLETE. Existing authoritative checks
# remain the owners of pass/fail truth.
tool-session-qualification:
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-tool-session-batches --max-units 1

tool-session-qualification-status:
	uv run --offline --frozen --no-sync --project backend oh-goon-tool-session-batches --status


coverage-python:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --workers 1 --batch-size 4 --batch-timeout 180 tests ../scripts/tests --marker "not certification" --coverage-source=src/oh_goon --coverage-data-file="$(CURDIR)/backend/.coverage" --coverage-fail-under=0 --coverage-report=json:../coverage-python.json

coverage-report:
	@test -f backend/.coverage || { echo "No backend coverage data exists; run make quality-check first." >&2; exit 2; }
	cd backend && .venv/bin/coverage report -m

# Focused SQLite behavior and resource-hygiene checks. Coverage is diagnostic;
# the complete Python measurement owns the separate quality ratchet.
test-sqlite-coverage:
	@mkdir -p "$(STATE_HOME)/oh-goon/coverage"
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --marker "" --workers 1 --batch-size 4 --batch-timeout 180 --pytest-arg=-W --pytest-arg=error::ResourceWarning --pytest-arg=-W --pytest-arg=error::pytest.PytestUnraisableExceptionWarning \
	  tests/unit/runtime/test_database_path_ownership.py \
	  tests/unit/runtime/test_execution_connection_scope.py \
	  tests/unit/runtime/test_execution_lease.py \
	  tests/unit/runtime/test_atomic_runs.py \
	  tests/unit/runtime/test_atomic_runtime.py \
	  tests/unit/runtime/test_db1b1_atomic_domain_operations.py \
	  tests/unit/runtime/test_db1b2_transaction_owner_and_replay.py \
	  tests/unit/runtime/test_db3_scout_backup_owner.py \
	  tests/unit/runtime/test_db5_backup_publication_failures.py \
	  tests/unit/runtime/test_preparation_cancellation.py \
	  tests/unit/runtime/test_revision_bound_admission.py \
	  tests/unit/runtime/test_sqlite_adversarial_identity.py \
	  tests/unit/runtime/test_sqlite_fault_matrix.py \
	  tests/unit/runtime/test_sqlite_authority_edge_cases.py \
	  --coverage-source=oh_goon.runtime.database \
	  --coverage-source=oh_goon.runtime.execution.sqlite_lease \
	  --coverage-source=oh_goon.runtime._sqlite_access_diagnostics \
	  --coverage-data-file="$(STATE_HOME)/oh-goon/coverage/sqlite-core.coverage" --coverage-fail-under=0 --coverage-report=term-missing \
	  --coverage-report=json:"$(STATE_HOME)/oh-goon/coverage/sqlite-core.json"

# Durable Journal checks cover fact integrity, transaction/lineage admission,
# pinned read cuts and recovery-facing writes. Projection coverage remains separate.
# Coverage is diagnostic; test failures and resource leaks fail this owner gate.
test-journal-coverage:
	@mkdir -p "$(STATE_HOME)/oh-goon/coverage"
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --marker "" --workers 1 --batch-size 4 --batch-timeout 180 --pytest-arg=-W --pytest-arg=error::ResourceWarning --pytest-arg=-W --pytest-arg=error::pytest.PytestUnraisableExceptionWarning \
	  tests/unit/runtime/test_journal.py \
	  tests/unit/runtime/test_journal_integrity.py \
	  tests/unit/runtime/test_agent_campaign_journal.py \
	  tests/unit/runtime/test_journal_read_views.py \
	  tests/unit/runtime/test_journal_operation_state.py \
	  tests/unit/runtime/test_readmission_lineage.py \
	  tests/unit/runtime/test_rerun_lineage.py \
	  tests/unit/runtime/test_scout_journal_changes.py \
	  tests/unit/runtime/test_recovery_reconciliation.py \
	  tests/unit/runtime/test_journal_reconciliation_authority.py \
	  tests/unit/runtime/test_atomic_runtime.py \
	  tests/unit/runtime/test_atomic_runs.py \
	  tests/unit/runtime/test_result_lifecycle.py \
	  tests/unit/runtime/test_crash_consistency.py \
	  tests/unit/runtime/test_durable_capability_provenance.py \
	  --coverage-source=oh_goon.runtime.journal \
	  --coverage-source=oh_goon.runtime.journal_store \
	  --coverage-source=oh_goon.runtime.journal_index \
	  --coverage-source=oh_goon.runtime.journal_codec \
	  --coverage-source=oh_goon.runtime.journal_facts \
	  --coverage-data-file="$(STATE_HOME)/oh-goon/coverage/journal-durability.coverage" --coverage-fail-under=0 --coverage-report=term-missing \
	  --coverage-report=json:"$(STATE_HOME)/oh-goon/coverage/journal-durability.json"

quality-check: coverage-python
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-quality-ratchet check --area python

quality-raise:
	@$(MAKE) coverage-python
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run test:coverage
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-quality-ratchet raise

test:
	@$(DEVELOPMENT_TEST_FRONTDOOR)

strict-executor-setup:
	@bash scripts/strict_executor_setup.sh

certify:
	@test -x "$(CERTIFICATION_RUNNER)" || { \
		printf '%s\n' \
		  'CERTIFICATION INITIALIZATION REQUIRED' \
		  'The canonical backend environment is not materialized.' \
		  'Run: make init' \
		  'No certification Goon ran.' >&2; \
		exit 2; \
	}
	@backend/.venv/bin/oh-goon-check-generated
	@$(CERTIFICATION_FRONTDOOR)

test-log:
	@uv run --offline --frozen --no-sync --project backend oh-goon-run-log --read "$(RUN_LOG_DIR)" test

certify-log:
	@uv run --offline --frozen --no-sync --project backend oh-goon-run-log --read "$(RUN_LOG_DIR)" certify

diagnostics-log:
	@path="$$(backend/.venv/bin/oh-goon-local-state diagnostics-path)"; \
	test -f "$$path" || { echo "No application diagnostic log exists yet: $$path" >&2; exit 2; }; \
	cat "$$path"

logs:
	@if [ ! -d "$(RUN_LOG_DIR)" ]; then echo "No Oh-Goon run logs exist yet."; else find "$(RUN_LOG_DIR)" -maxdepth 2 -type f -name '*.log' -printf '%TY-%Tm-%Td %TH:%TM  %p\n' | sort -r; fi

check-backend: check-backend-quality check-backend-tests

check-backend-quality:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh uv run --offline --frozen --no-sync --group quality ruff check src tests
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh uv run --offline --frozen --no-sync --group quality ruff format --check src tests
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh uv run --offline --frozen --no-sync --group quality mypy
	$(DEVELOPMENT_RESOURCE_RUNNER) uv run --offline --frozen --no-sync --project backend oh-goon-quality-ratchet guard

check-backend-tests:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd backend && exec "$$@"' sh .venv/bin/oh-goon-test-batches --qualified --show-output --pytest-config pyproject.toml --granularity tests --workers $(BACKEND_CHECK_TEST_WORKERS) --batch-size 64 --batch-timeout 180 --marker "$(CHECK_PYTEST_MARKER)" $(PYTEST_REPORT_ARGUMENT) $(BACKEND_CHECK_TEST_ROOTS)

backend-check: check-backend

check-tooling: check-tooling-quality check-tooling-tests

check-tooling-quality:
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'TOOLING QUALITY [1/4]' 'Owner: ruff | lint' -- uv run --offline --frozen --no-sync --group quality --project backend ruff check scripts tests/integration
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'TOOLING QUALITY [2/4]' 'Owner: ruff | format check' -- uv run --offline --frozen --no-sync --group quality --project backend ruff format --check scripts tests/integration
	@$(DEVELOPMENT_RESOURCE_RUNNER) $(RUN_STEP) 'TOOLING QUALITY [3/4]' 'Owner: mypy | type check' -- uv run --offline --frozen --no-sync --group quality --project backend mypy --config-file backend/pyproject.toml scripts/tests tests/integration

check-tooling-tests:
	@$(RUN_STEP) 'TOOLING QUALITY [4/4]' 'Owner: Oh-Goon | bounded tooling tests' -- $(MAKE) test-tooling-complete TOOLING_COMPLETE_TEST_MARKER="$(CHECK_PYTEST_MARKER)"

tooling-check: check-tooling

check-frontend: theme-contrast
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run check

frontend-check: check-frontend

check:
	@OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh check $(DEVELOPMENT_RESOURCE_RUNNER) $(DEVELOPMENT_CHECK_RUNNER)

# Exhaustive diagnostic projection of every canonical APP certification Goon.
# Independent failures continue so one run reports the whole application state.
# This target cannot create APP PASS; `make certify` remains unchanged.
verify:
	@OH_GOON_RUN_LOG_DIR="$(RUN_LOG_DIR)" bash scripts/run_logged.sh verify $(DEVELOPMENT_RESOURCE_RUNNER) $(APPLICATION_VERIFY_RUNNER)

build-release:
	bash scripts/release/build_release.sh

# GitHub owns the prerequisite checks for this explicit assembly-only action.
# Local validated release builds retain the complete build-release journey.
assemble-release:
	bash scripts/release/build_release.sh assemble

# Exhaustive portable regression is a release action, separate from APP/native qualification.
release-check:
	$(MAKE) -k $(RELEASE_CHECK_TARGETS) BACKEND_CHECK_TEST_ROOTS="tests/unit tests/dependency_complete" CHECK_PYTEST_MARKER="not certification" PYTEST_RESUME=""

# This list is generated from the original Make owner, never a second CI registry.
release-check-plan:
	@printf '%s\n' '$(RELEASE_CHECK_TARGETS)' | awk 'BEGIN {printf "["} {for (i=1;i<=NF;i++) printf "%s\"%s\"", i==1 ? "" : ",", $$i} END {print "]"}'

acceptance-release:
	$(MAKE) repository-understanding-exam
	$(MAKE)
	$(MAKE) release-accept


acceptance-browser-container:
	uv run --offline --frozen --no-sync --project backend oh-goon-browser-acceptance-container run

acceptance-container:
	$(MAKE) docker-accept
	$(MAKE) docker-accept-opensuse

acceptance-product:
	uv run --offline --frozen --no-sync --project backend oh-goon-product-acceptance

acceptance-scale:
	$(MAKE) scout-network-certification
	$(MAKE) scout-proxy-certification
	$(MAKE) scout-overhead-certification
	$(MAKE) scout-crash-restart-certification
	$(MAKE) scout-failure-isolation-certification
	$(MAKE) scout-corruption-recovery-certification
	$(MAKE) scout-retention-checkpoint-certification

release-accept:
	uv run --offline --frozen --no-sync --project backend oh-goon-release-acceptance

docker-prepare: release-locks-check
	mkdir -p "$${OH_GOON_DATA_DIR:-.oh-goon-data}" "$${OH_GOON_REPOSITORIES_DIR:-repositories}"

docker-up: docker-prepare
	OH_GOON_UID=$$(id -u) OH_GOON_GID=$$(id -g) docker compose up --build

docker-up-opensuse: docker-prepare
	OH_GOON_UID=$$(id -u) OH_GOON_GID=$$(id -g) OH_GOON_DOCKERFILE=docker/opensuse/Dockerfile docker compose up --build

docker-down:
	docker compose down

docker-accept:
	uv run --offline --frozen --no-sync --project backend oh-goon-container-acceptance --dockerfile docker/ubuntu/Dockerfile

docker-accept-opensuse:
	uv run --offline --frozen --no-sync --project backend oh-goon-container-acceptance --dockerfile docker/opensuse/Dockerfile

scout-load-probe:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-load-probe

scout-http-load-probe:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-http-load-probe

scout-network-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-network-certification

scout-proxy-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-reverse-proxy-certification

scout-overhead-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-runtime-overhead-certification

scout-crash-restart-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-crash-restart-certification

scout-failure-isolation-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-failure-isolation-certification

scout-corruption-recovery-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-corruption-recovery-certification

scout-retention-checkpoint-certification:
	uv run --offline --frozen --no-sync --project backend oh-goon-scout-retention-checkpoint-certification

# Development-campaign recovery authority. RECOVERY_STORE must be durable outside
# the disposable working tree. Continuation input is authoring intent; the recovery
# owner binds actual source/archive/proof identities into immutable continuation state
# before receipt publication and HEAD CAS.
RECOVERY_FORMAT ?= json

recovery-status:
	@test -n "$(RECOVERY_STORE)" || { echo "RECOVERY_STORE=<durable-path> is required" >&2; exit 2; }
	@backend/.venv/bin/oh-goon-recovery-checkpoint verify --store "$(RECOVERY_STORE)" --subject-root "$(CURDIR)" --format "$(RECOVERY_FORMAT)"

recovery-checkpoint:
	@test -n "$(RECOVERY_STORE)" || { echo "RECOVERY_STORE=<durable-path> is required" >&2; exit 2; }
	@test -n "$(RECOVERY_CAMPAIGN_ID)" || { echo "RECOVERY_CAMPAIGN_ID=<campaign> is required" >&2; exit 2; }
	@test -n "$(RECOVERY_EXPECTED_GENERATION)" || { echo "RECOVERY_EXPECTED_GENERATION=<generation> is required" >&2; exit 2; }
	@test -n "$(RECOVERY_CONTINUATION_INPUT)" || { echo "RECOVERY_CONTINUATION_INPUT=<external-continuation-input.json> is required" >&2; exit 2; }
	@backend/.venv/bin/oh-goon-recovery-checkpoint checkpoint --root "$(CURDIR)" --store "$(RECOVERY_STORE)" --campaign-id "$(RECOVERY_CAMPAIGN_ID)" --expected-generation "$(RECOVERY_EXPECTED_GENERATION)" --continuation-input "$(RECOVERY_CONTINUATION_INPUT)" --format "$(RECOVERY_FORMAT)" $(if $(RECOVERY_EXPECTED_HEAD_SHA256),--expected-head-sha256 "$(RECOVERY_EXPECTED_HEAD_SHA256)",)

recovery-resume:
	@test -n "$(RECOVERY_STORE)" || { echo "RECOVERY_STORE=<durable-path> is required" >&2; exit 2; }
	@test -n "$(RECOVERY_DESTINATION)" || { echo "RECOVERY_DESTINATION=<empty-path> is required" >&2; exit 2; }
	@backend/.venv/bin/oh-goon-recovery-checkpoint resume --store "$(RECOVERY_STORE)" --destination "$(RECOVERY_DESTINATION)" --subject-root "$(CURDIR)" --format "$(RECOVERY_FORMAT)"

# M215: a small, original-Vitest-owned frontend failure locator. Execute from
# the locked npm materialization produced by make sync; no fallback npx fetch.
# Not release authority and never a substitute for the full frontend suite.
frontend-schedule-plan-input-regression:
	$(DEVELOPMENT_RESOURCE_RUNNER) sh -c 'cd frontend && exec "$$@"' sh npm run test -- src/features/schedules/components/ScheduleCreatePanel.test.tsx src/features/schedules/components/schedulePlanInputDraft.test.ts

# M209: diagnostic-only WSL evidence frontdoor. This does not replace the
# canonical make init/test/certify or grant enrollment/release authority.
qualify-native-wsl:
	@bash scripts/qualify_native_wsl.sh "$(QUALIFY_EVIDENCE_DIR)"

# B12: remove only the existing narrowly-classified disposable transport
# artifacts before snapshotting source or executing any development witness.
# A failed cleanup aborts qualification; this is NOT a production admission gate.
.PHONY: qualify-development-wsl
qualify-development-wsl:
	@$(MAKE) --no-print-directory source-transport-clean
	@bash scripts/qualify_b12_development_wsl.sh "$(QUALIFY_EVIDENCE_DIR)" "$(QUALIFY_SOURCE_MANIFEST)"
