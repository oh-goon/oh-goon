"""B12: Windows metadata remains auditable; supervisor never promotes a green XML alone."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "backend/src/oh_goon/repository_tooling/b12_transport_audit.py"
WITNESS = ROOT / "scripts/qualify_b12_development_wsl.sh"
SUPERVISOR = ROOT / "backend/src/oh_goon/repository_tooling/b11_ring_supervisor.py"
spec = importlib.util.spec_from_file_location("b12_audit", AUDIT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _candidate(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "src.py").write_text('print("hello")\n')
    return source


def test_realistic_ads_sidecar_preserves_exact_authored_inventory(tmp_path):
    src = _candidate(tmp_path)
    without = module.scan(src)
    sidecar = src / "src.py:Zone.Identifier"
    sidecar.write_text("[ZoneTransfer]\r\nZoneId=3\r\n")
    result = module.scan(src)
    assert result["unsafe_entries"] == []
    assert result["source_members"] == 1
    assert result["sidecar_count"] == 1
    assert result["inventory"] == without["inventory"]
    assert result["windows_sidecars"][0]["sha256"]
    assert sidecar.exists(), "audit must never remove transport evidence"


def test_windows_stream_data_suffix_is_known_only_with_original_file(tmp_path):
    src = _candidate(tmp_path)
    sidecar = src / "src.py:Zone.Identifier:$DATA"
    sidecar.write_text("[ZoneTransfer]\nZoneId=3\n")
    assert module.scan(src)["sidecar_count"] == 1
    (src / "src.py").unlink()
    assert module.scan(src)["unsafe_entries"] == ["./src.py:Zone.Identifier:$DATA"]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"MOTW",
        b"not an INI header",
        b"\xff\xfe[\x00Z\x00o\x00n\x00e\x00",
        b"\x00" * 20000,
    ],
)
def test_exact_ads_suffix_is_transport_even_with_opaque_windows_payload(
    tmp_path, payload
):
    """The canonical suffix, not Windows-specific payload decoding, owns transport classification."""
    src = _candidate(tmp_path)
    original = module.scan(src)["inventory"]
    sidecar = src / "src.py:Zone.Identifier"
    sidecar.write_bytes(payload)
    result = module.scan(src)
    assert not result["unsafe_entries"]
    assert result["source_members"] == 1 and result["sidecar_count"] == 1
    assert result["inventory"] == original
    assert result["windows_sidecars"][0]["content_authority"] == "NONE"


def test_orphaned_sidecar_remains_unsafe_even_with_exact_suffix(tmp_path):
    src = _candidate(tmp_path)
    sidecar = src / "missing.py:Zone.Identifier"
    sidecar.write_bytes(b"MOTW")
    assert module.scan(src)["unsafe_entries"] == ["./missing.py:Zone.Identifier"]


def test_directory_owner_sidecar_is_recognized(tmp_path):
    src = _candidate(tmp_path)
    (src / "directory").mkdir()
    (src / "directory:Zone.Identifier").write_bytes(b"")
    result = module.scan(src)
    assert not result["unsafe_entries"] and result["sidecar_count"] == 1


def test_symlink_and_hardlink_source_substitution_fail(tmp_path):
    src = _candidate(tmp_path)
    (src / "symlink.py").symlink_to(src / "src.py")
    assert module.scan(src)["unsafe_entries"]
    (src / "symlink.py").unlink()
    (src / "hardlink.py").hardlink_to(src / "src.py")
    assert module.scan(src)["unsafe_entries"]


def test_unrecognized_extra_is_not_hidden_as_known_windows_metadata(tmp_path):
    src = _candidate(tmp_path)
    original = module.scan(src)["inventory"]
    (src / "src.py:UnknownData").write_text("unknown\n")
    updated = module.scan(src)
    assert updated["source_members"] == 2 and updated["sidecar_count"] == 0
    assert updated["inventory"] != original


def test_source_audit_baseline_requires_full_membership_not_subset(tmp_path, capsys):
    src = _candidate(tmp_path)
    baseline = tmp_path / "authoritative.sha256"
    baseline.write_text(module.scan(src)["inventory"])
    assert module.main(["audit", "--root", str(src), "--baseline", str(baseline)]) == 0
    (src / "unexpected.py").write_text("surprise\n")
    assert module.main(["audit", "--root", str(src), "--baseline", str(baseline)]) == 3
    assert "MISMATCH" in capsys.readouterr().out


def test_script_preserves_one_command_and_audits_both_ends():
    script = WITNESS.read_text()
    assert "TRANSPORT_PRE.json" in script and "TRANSPORT_POST.json" in script
    assert "b12_transport_audit" in script
    assert "QUALIFY_STRESS_REPEATS" in script
    assert "source_integrity=FAIL" in script
    assert "physical_lace=NOT_PROVEN" in script
    assert "external_issuer=NOT_PROVEN" in script
    assert "release_authority=NONE" in script
    makefile = (ROOT / "Makefile").read_text()
    assert "qualify_b12_development_wsl.sh" in makefile
    assert ".checkpoint/B14_SOURCE.sha256" in script


def test_qualification_make_target_cleans_transport_before_witness_and_fails_closed(
    tmp_path,
):
    """Test the *real target recipe* with isolated fake shell scripts, not a mock order list.

    Neither a failed cleanup nor an early source audit may start test work. This
    fixture never invokes the actual project's cleanup/test script or its locks.
    """
    makefile_text = (ROOT / "Makefile").read_text(encoding="utf-8")
    start = makefile_text.index("\nqualify-development-wsl:\n") + 1
    recipe = makefile_text[start:].split("\n\n", 1)[0]
    assert "source-transport-clean" in recipe
    assert recipe.index("source-transport-clean") < recipe.index(
        "qualify_b12_development_wsl.sh"
    )

    fake = tmp_path / "fake-checkout"
    (fake / "scripts").mkdir(parents=True)
    (fake / "Makefile").write_text(
        ".PHONY: source-transport-clean qualify-development-wsl\n"
        "source-transport-clean:\n\t@bash scripts/source_transport_clean.sh\n\n"
        + recipe
        + "\n",
        encoding="utf-8",
    )
    (fake / "scripts/source_transport_clean.sh").write_text(
        "#!/bin/sh\n"
        'echo CLEAN >> "$OH_GOON_TEST_ORDER_LOG"\n'
        'if [ "${OH_GOON_TEST_CLEAN_FAILURE:-0}" = 1 ]; then exit 7; fi\n',
        encoding="utf-8",
    )
    (fake / "scripts/qualify_b12_development_wsl.sh").write_text(
        '#!/bin/sh\necho WITNESS >> "$OH_GOON_TEST_ORDER_LOG"\n', encoding="utf-8"
    )

    import os

    log = tmp_path / "order.log"
    env = {**os.environ, "OH_GOON_TEST_ORDER_LOG": str(log)}
    ok = subprocess.run(
        ["make", "--no-print-directory", "qualify-development-wsl"],
        cwd=fake,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert ok.returncode == 0, ok.stderr
    assert log.read_text().splitlines() == ["CLEAN", "WITNESS"]

    log.unlink()
    env["OH_GOON_TEST_CLEAN_FAILURE"] = "1"
    blocked = subprocess.run(
        ["make", "--no-print-directory", "qualify-development-wsl"],
        cwd=fake,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert blocked.returncode != 0
    assert log.read_text().splitlines() == ["CLEAN"]


def test_transport_clean_accepts_extracted_windows_sidecars_and_preserves_source(
    tmp_path,
):
    """Reproduces B12 native WSL blocker: valid ADS names need not contain ZoneTransfer INI."""
    root = tmp_path / "checkout"
    (root / "backend").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "backend/pyproject.toml").write_text('[project]\nname = "test"\n')
    (root / "VERSION").write_text("0.0.0\n")
    (root / "AGENTS.md").write_text("Authored\n")
    (root / "README.md").write_text("Authored readme\n")
    original = (root / "AGENTS.md").read_bytes()
    variants = [
        b"MOTW",
        b"",
        b"opaque Windows extraction data",
        "[ZoneTransfer]\r\nZoneId=3\r\n".encode("utf-16"),
    ]
    for i, payload in enumerate(variants):
        owner = root / f"authored_{i}.txt"
        owner.write_bytes(original)
        (root / f"authored_{i}.txt:Zone.Identifier").write_bytes(payload)
    (root / "backend:Zone.Identifier").write_bytes(b"MOTW")  # directory stream
    shutil.copy2(
        ROOT / "scripts/source_transport_clean.sh",
        root / "scripts/source_transport_clean.sh",
    )
    clean = subprocess.run(
        ["bash", str(root / "scripts/source_transport_clean.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert clean.returncode == 0, clean.stderr
    assert "Removed 5 disposable source transport artifacts" in clean.stdout
    for i in range(len(variants)):
        assert (root / f"authored_{i}.txt").read_bytes() == original
        assert not (root / f"authored_{i}.txt:Zone.Identifier").exists()
    assert (root / "backend").is_dir()
    assert (root / "AGENTS.md").read_bytes() == original
    again = subprocess.run(
        ["bash", str(root / "scripts/source_transport_clean.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert again.returncode == 0 and "Removed 0" in again.stdout


def test_transport_clean_rejects_orphans_and_symlinked_owners_atomically(tmp_path):
    root = tmp_path / "checkout"
    (root / "backend").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "backend/pyproject.toml").write_text('[project]\nname = "test"\n')
    (root / "VERSION").write_text("0.0.0\n")
    (root / "good.py").write_text("x=1\n")
    good = root / "good.py:Zone.Identifier"
    good.write_bytes(b"MOTW")
    orphan = root / "missing.py:Zone.Identifier"
    orphan.write_bytes(b"MOTW")
    shutil.copy2(
        ROOT / "scripts/source_transport_clean.sh",
        root / "scripts/source_transport_clean.sh",
    )
    denied = subprocess.run(
        ["bash", str(root / "scripts/source_transport_clean.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert denied.returncode == 2
    assert "missing.py:Zone.Identifier" in denied.stderr
    assert good.is_file() and orphan.is_file()
    orphan.unlink()
    (root / "fake.py").symlink_to(root / "good.py")
    linked = root / "fake.py:Zone.Identifier"
    linked.write_bytes(b"")
    denied = subprocess.run(
        ["bash", str(root / "scripts/source_transport_clean.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert denied.returncode == 2
    assert good.is_file() and linked.is_file()


def test_supervisor_carries_birth_and_post_teardown_process_evidence(tmp_path):
    spec2 = importlib.util.spec_from_file_location("b11_ring_supervisor", SUPERVISOR)
    assert spec2 and spec2.loader
    sup = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(sup)
    code = "from pathlib import Path; Path(%r).write_text(%r)" % (
        str(tmp_path / "RING.xml"),
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="ok" /></testsuite></testsuites>',
    )
    r = sup.supervise(
        name="RING",
        command=[sys.executable, "-c", code],
        directory=tmp_path,
        timeout_seconds=3,
    )
    assert r["accepted"]
    assert "initial_process_identity" in r
    assert r["lingering_processes_after_teardown"] == []
    assert r["teardown_signals"] == []
    assert r["release_authority"] == "NONE"


def test_witness_help_and_non_wsl_fail_before_work(tmp_path):
    root = tmp_path / "candidate"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(WITNESS, root / "scripts/qualify_b12_development_wsl.sh")
    help_result = subprocess.run(
        ["bash", str(root / "scripts/qualify_b12_development_wsl.sh"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    if "microsoft" not in Path("/proc/sys/kernel/osrelease").read_text().lower():
        evidence = tmp_path / "evidence"
        denied = subprocess.run(
            [
                "bash",
                str(root / "scripts/qualify_b12_development_wsl.sh"),
                str(evidence),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert denied.returncode == 3 and not evidence.exists()


def test_duplicate_junit_cases_rejected_despite_green_suite_totals(tmp_path):
    spec2 = importlib.util.spec_from_file_location("b11_ring_supervisor", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec2)
    assert spec2 and spec2.loader
    spec2.loader.exec_module(sup)
    xml = (
        '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testcase classname="pkg" name="same"/><testcase classname="pkg" name="same"/>'
        "</testsuite></testsuites>"
    )
    path = tmp_path / "duplicates.xml"
    path.write_text(xml)
    report = sup._junit_evidence(path)
    assert not report["valid"] and report["reason"].startswith("INVALID_JUNIT")


def test_linux_process_birth_identity_recorded_for_live_process():
    spec2 = importlib.util.spec_from_file_location("b11_ring_supervisor", SUPERVISOR)
    sup = importlib.util.module_from_spec(spec2)
    assert spec2 and spec2.loader
    spec2.loader.exec_module(sup)
    import os

    report = sup._process_details([os.getpid()])[0]
    assert report["pid"] == os.getpid()
    assert report["birth_ticks"] > 0 and report["session_id"] >= 0


def test_native_wsl_manifest_selector_uses_explicit_archive_fixture(tmp_path):
    """Exercise archive precedence without requiring history in the Git checkout."""
    checkpoint = tmp_path / ".checkpoint"
    checkpoint.mkdir()
    for name in ("B11", "CO_R3T", "CO_R3U2B2B2B1"):
        (checkpoint / f"{name}_SOURCE.sha256").write_text("archive fixture\n")
    script = WITNESS.read_text(encoding="utf-8")
    selection = (
        script.split("# Packaged-source exact-member gate", 1)[1]
        .split("\n", 1)[1]
        .split('if [[ -f "$embedded" ]]', 1)[0]
    )
    completed = subprocess.run(
        ["bash", "-euc", selection + "\nprintf '%s\\n' \"$embedded\""],
        cwd=tmp_path,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"BASH_ENV", "ENV"}
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == ".checkpoint/CO_R3U2B2B2B1_SOURCE.sha256"


def _isolated_qualifier_fragment(script: str, section: str) -> str:
    """Exercise the real shell function without bypassing the native WSL gate.

    No project subprocess or privileged host mutation is performed. Fake uv
    creates exactly the exit/receipt behavior that the ring needs to classify.
    """
    sample = (
        "qualify_host_sample() {"
        + script.split("qualify_host_sample() {", 1)[1].split(
            "# Canonical qualification environment transaction:", 1
        )[0]
    )
    if section == "sync":
        body = (
            "# Canonical qualification environment transaction:"
            + script.split("# Canonical qualification environment transaction:", 1)[
                1
            ].split("# The ring is supervised", 1)[0]
        )
    elif section == "ring":
        body = script.split("run_ring() {", 1)[1].split(
            "last_result=0\n# Fail-before-work", 1
        )[0]
        body = "run_ring() {" + body
    else:
        raise AssertionError(section)
    return sample + body


def test_wsl_qualifier_fails_fast_on_killed_launcher_with_host_receipts(tmp_path):
    """Reproduces six repeated SIGKILLs as one infra loss, not six false test failures."""
    import os

    script = WITNESS.read_text()
    assert script.count("uv sync --locked --group dev") == 1
    assert (
        '"$qualification_python" -m oh_goon.repository_tooling.b11_ring_supervisor'
        in script
    )
    assert "uv sync --check --locked --offline --group dev" in script
    assert 'source_transport_audit > "$evidence/TRANSPORT_POST.json"' in script
    env_dir = tmp_path / "evidence"
    env_dir.mkdir()
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    (tmp_path / "backend/.venv/bin").mkdir(parents=True)
    launcher = tmp_path / "backend/.venv/bin/python"
    launcher.write_text("#!/bin/sh\nkill -KILL $$\n")
    launcher.chmod(0o755)
    uv_exe = fake / "uv"
    uv_exe.write_text("#!/bin/sh\necho WRONG_IN_RING >&2; exit 99\n")
    uv_exe.chmod(0o755)
    fixture = tmp_path / "run.sh"
    fixture.write_text(
        'set -Eeuo pipefail\nroot="$PWD"\nevidence="$1"\nqualification_python="$PWD/backend/.venv/bin/python"\n'
        + _isolated_qualifier_fragment(script, "ring")
        + "\nrun_ring CORE tests/nonexistent.py\necho WRONG_CONTINUED\n"
    )
    run = subprocess.run(
        ["bash", str(fixture), str(env_dir)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(fake) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert run.returncode == 1, (run.stdout, run.stderr)
    assert "WRONG_CONTINUED" not in run.stdout
    assert (
        "INFRA_BLOCKED=CORE exit=137 missing_receipt=1"
        in (env_dir / "INFRA_FAILURE.txt").read_text()
    )
    assert "CORE=137 missing_receipt=1" in (env_dir / "RING_RESULTS.txt").read_text()
    assert "MemAvailable:" in (env_dir / "CORE_BEFORE_HOST.txt").read_text()
    assert (env_dir / "CORE_AFTER_HOST.txt").exists()


def test_wsl_qualifier_environment_bootstrap_failure_stops_before_tests(tmp_path):
    """One failed locked sync must not produce repeated ring-side dependency resolution."""
    import os

    script = WITNESS.read_text()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (tmp_path / "backend").mkdir()
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    uv_exe = fake / "uv"
    uv_exe.write_text("#!/bin/sh\nexit 17\n")
    uv_exe.chmod(0o755)
    fixture = tmp_path / "sync.sh"
    fixture.write_text(
        'set -Eeuo pipefail\nroot="$PWD"\nevidence="$1"\n'
        + _isolated_qualifier_fragment(script, "sync")
        + "\necho WRONG_CONTINUED\n"
    )
    run = subprocess.run(
        ["bash", str(fixture), str(evidence)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(fake) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert run.returncode == 1, (run.stdout, run.stderr)
    assert "WRONG_CONTINUED" not in run.stdout
    assert (
        "INFRA_BLOCKED=UV_LOCKED_SYNC exit=17"
        in (evidence / "INFRA_FAILURE.txt").read_text()
    )
    assert "UV_LOCKED_SYNC=17" in (evidence / "RING_RESULTS.txt").read_text()
    assert (evidence / "UV_SYNC_BEFORE_HOST.txt").exists()
    assert (evidence / "UV_SYNC_AFTER_HOST.txt").exists()
