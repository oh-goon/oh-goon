from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

ROOT = Path(__file__).resolve().parents[2]
SOURCE_LAYOUT = ROOT / "backend/.venv/bin/oh-goon-source-layout"
FRONTDOORS = (
    ("development_check.sh", "change-check"),
    ("development_test.sh", "frontend-build"),
    ("application_verify.sh", "browser-required-certification"),
)
pytestmark = pytest.mark.process

_OWNER_DRIVER = """from pathlib import Path
import os, sys
target = sys.argv[-1]
with Path(os.environ['OH_GOON_TEST_CALLS']).open('a') as stream:
    stream.write(target + '\\n')
if target == os.environ.get('OH_GOON_TEST_MUTATE_ON'):
    mode = os.environ['OH_GOON_TEST_MUTATION']
    if mode == 'source':
        path = Path(os.environ['OH_GOON_TEST_SOURCE'])
        path.write_bytes(path.read_bytes().replace(b'one', b'two'))
    elif mode == 'ephemeral':
        for name in ('backend/.venv/state', 'frontend/node_modules/state', '.pytest_cache/state'):
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('operational output')
    elif mode == 'replace':
        root = Path.cwd()
        root.rename(root.with_name('retained-checkout'))
        root.symlink_to(os.environ['OH_GOON_TEST_REPLACEMENT'], target_is_directory=True)
    elif mode == 'symlink':
        path = Path(os.environ['OH_GOON_TEST_SOURCE'])
        path.unlink()
        path.symlink_to(os.environ['OH_GOON_TEST_REPLACEMENT'])
if target == os.environ.get('OH_GOON_TEST_FAIL_TARGET'):
    sys.exit(19)
if target == os.environ.get('OH_GOON_TEST_RESTORE_ON'):
    Path(os.environ['OH_GOON_TEST_SOURCE']).write_text('one\\n')
"""


def _fixture(tmp_path: Path, script: str) -> tuple[Path, dict[str, str], Path]:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in (script, "command_banner.sh", "host_python.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    for relative in (
        "README.md",
        "backend/src/module.py",
        "backend/tests/unit/test_case.py",
        "frontend/src/component.ts",
        "generated/openapi/openapi.json",
        "scripts/owned-check.sh",
    ):
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("one\n", encoding="utf-8")
    binary = checkout / "backend/.venv/bin/oh-goon-source-layout"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(SOURCE_LAYOUT)
    calls = tmp_path / "calls"
    driver = tmp_path / "owner.py"
    driver.write_text(_OWNER_DRIVER, encoding="utf-8")
    make = tmp_path / "make"
    make.write_text(
        '#!/bin/sh\nexec "$OH_GOON_TEST_PYTHON" "$OH_GOON_TEST_DRIVER" "$@"\n',
        encoding="utf-8",
    )
    make.chmod(0o755)
    env = {
        **os.environ,
        "MAKE": str(make),
        "OH_GOON_TEST_PYTHON": sys.executable,
        "OH_GOON_TEST_DRIVER": str(driver),
        "OH_GOON_TEST_CALLS": str(calls),
        "OH_GOON_CONSTRAINED_HOST": "0",
        "OH_GOON_RUN_HOSTED_DIAGNOSTIC": "0",
    }
    return checkout, env, calls


def _run(
    checkout: Path, script: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(checkout / "scripts" / script)],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


@pytest.mark.parametrize(("script", "last"), FRONTDOORS)
def test_frontdoor_rejects_source_mutation_by_its_final_successful_owner(
    tmp_path: Path, script: str, last: str
) -> None:
    checkout, env, _calls = _fixture(tmp_path, script)
    before = canonical_source_identity(checkout)
    baseline = _run(checkout, script, env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert before in baseline.stdout
    assert "RESULT: PASSED" in baseline.stdout
    env.update(
        OH_GOON_TEST_MUTATE_ON=last,
        OH_GOON_TEST_MUTATION="source",
        OH_GOON_TEST_SOURCE="README.md",
    )
    changed = _run(checkout, script, env)
    after = canonical_source_identity(checkout)
    assert before != after
    assert changed.returncode != 0
    assert "RESULT: PASSED" not in changed.stdout
    assert before in changed.stdout + changed.stderr
    assert after in changed.stderr


@pytest.mark.parametrize(
    "relative",
    (
        "backend/src/module.py",
        "backend/tests/unit/test_case.py",
        "frontend/src/component.ts",
        "generated/openapi/openapi.json",
        "scripts/owned-check.sh",
    ),
)
def test_authored_owner_mutations_between_checks_cannot_share_a_pass(
    tmp_path: Path, relative: str
) -> None:
    script = "development_check.sh"
    checkout, env, _calls = _fixture(tmp_path, script)
    baseline = _run(checkout, script, env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    env.update(
        OH_GOON_TEST_MUTATE_ON="change-check",
        OH_GOON_TEST_MUTATION="source",
        OH_GOON_TEST_SOURCE=relative,
    )
    changed = _run(checkout, script, env)
    assert changed.returncode != 0
    assert "RESULT: PASSED" not in changed.stdout
    assert "Expected: sha256:" in changed.stderr
    assert "Observed: sha256:" in changed.stderr


@pytest.mark.parametrize(("script", "last"), FRONTDOORS)
def test_frontdoor_ignores_ephemeral_outputs_and_preserves_native_failures(
    tmp_path: Path, script: str, last: str
) -> None:
    checkout, env, calls = _fixture(tmp_path, script)
    before = canonical_source_identity(checkout)
    env.update(OH_GOON_TEST_MUTATE_ON=last, OH_GOON_TEST_MUTATION="ephemeral")
    stable = _run(checkout, script, env)
    assert stable.returncode == 0, stable.stdout + stable.stderr
    assert canonical_source_identity(checkout) == before
    env["OH_GOON_TEST_FAIL_TARGET"] = last
    failed = _run(checkout, script, env)
    assert failed.returncode == 1
    assert "RESULT: PASSED" not in failed.stdout
    assert "exit 19" in failed.stdout
    assert last in calls.read_text().splitlines()


@pytest.mark.parametrize(("script", "last"), FRONTDOORS)
def test_frontdoor_rejects_replaced_checkout_even_with_identical_source_bytes(
    tmp_path: Path, script: str, last: str
) -> None:
    checkout, env, _calls = _fixture(tmp_path, script)
    baseline = _run(checkout, script, env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    replacement = tmp_path / "replacement"
    shutil.copytree(checkout, replacement, symlinks=True)
    assert canonical_source_identity(checkout) == canonical_source_identity(replacement)
    env.update(
        OH_GOON_TEST_MUTATE_ON=last,
        OH_GOON_TEST_MUTATION="replace",
        OH_GOON_TEST_REPLACEMENT=str(replacement),
    )
    changed = _run(checkout, script, env)
    assert changed.returncode != 0
    assert "RESULT: PASSED" not in changed.stdout
    assert "declared checkout directory changed" in changed.stderr


@pytest.mark.parametrize(("script", "_last"), FRONTDOORS)
def test_missing_source_owner_refuses_checks_before_authoritative_work(
    tmp_path: Path, script: str, _last: str
) -> None:
    checkout, env, calls = _fixture(tmp_path, script)
    (checkout / "backend/.venv/bin/oh-goon-source-layout").unlink()
    failed = _run(checkout, script, env)
    assert failed.returncode != 0
    assert "RESULT: PASSED" not in failed.stdout
    invoked = calls.read_text().splitlines() if calls.exists() else []
    assert invoked == (["init"] if script == "application_verify.sh" else [])


def test_source_layout_identity_explicitly_selects_checkout_without_repair_authority(
    tmp_path: Path,
) -> None:
    checkout, env, _calls = _fixture(tmp_path, "development_check.sh")
    inspected = subprocess.run(
        [str(SOURCE_LAYOUT), "identity", "--root", str(checkout)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert inspected.stdout.strip() == canonical_source_identity(checkout)
    refused = subprocess.run(
        [str(SOURCE_LAYOUT), "transport-clean", "--root", str(checkout)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert refused.returncode == 2


def test_initial_checkout_alias_resolves_to_the_selected_physical_source(
    tmp_path: Path,
) -> None:
    checkout, env, _calls = _fixture(tmp_path, "development_check.sh")
    alias = tmp_path / "operator-alias"
    alias.symlink_to(checkout, target_is_directory=True)
    result = _run(alias, "development_check.sh", env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert canonical_source_identity(checkout) in result.stdout


def test_source_that_becomes_unreadable_cannot_publish_aggregate_pass(
    tmp_path: Path,
) -> None:
    checkout, env, _calls = _fixture(tmp_path, "development_check.sh")
    baseline = _run(checkout, "development_check.sh", env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    external = tmp_path / "external.py"
    external.write_text("unowned bytes\n", encoding="utf-8")
    env.update(
        OH_GOON_TEST_MUTATE_ON="change-check",
        OH_GOON_TEST_MUTATION="symlink",
        OH_GOON_TEST_SOURCE="backend/src/module.py",
        OH_GOON_TEST_REPLACEMENT=str(external),
    )
    result = _run(checkout, "development_check.sh", env)
    assert result.returncode != 0
    assert "RESULT: PASSED" not in result.stdout
    assert "refuses symlinks" in result.stderr


@pytest.mark.parametrize(("script", "last"), FRONTDOORS[1:])
def test_observed_source_change_blocks_later_owner_that_would_restore_old_bytes(
    tmp_path: Path, script: str, last: str
) -> None:
    checkout, env, calls = _fixture(tmp_path, script)
    baseline = _run(checkout, script, env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    calls.unlink()
    first = (
        "test-python-complete" if script == "application_verify.sh" else "test-python"
    )
    env.update(
        OH_GOON_TEST_MUTATE_ON=first,
        OH_GOON_TEST_MUTATION="source",
        OH_GOON_TEST_SOURCE="README.md",
        OH_GOON_TEST_RESTORE_ON=last,
    )
    result = _run(checkout, script, env)
    assert result.returncode == 1
    assert "RESULT: PASSED" not in result.stdout
    invoked = calls.read_text().splitlines()
    assert invoked == (
        ["init", "candidate-convergence-preflight", first]
        if script == "application_verify.sh"
        else [first]
    )
    assert (checkout / "README.md").read_text() == "two\n"
    assert "BLOCKED" in result.stdout


def test_verify_refuses_changed_convergence_source_before_expensive_owners(
    tmp_path: Path,
) -> None:
    checkout, env, calls = _fixture(tmp_path, "application_verify.sh")
    baseline = _run(checkout, "application_verify.sh", env)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    calls.unlink()
    env.update(
        OH_GOON_TEST_MUTATE_ON="candidate-convergence-preflight",
        OH_GOON_TEST_MUTATION="source",
        OH_GOON_TEST_SOURCE="README.md",
    )
    result = _run(checkout, "application_verify.sh", env)
    assert result.returncode == 1
    assert "RESULT: PASSED" not in result.stdout
    assert calls.read_text().splitlines() == ["init", "candidate-convergence-preflight"]
