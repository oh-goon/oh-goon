from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_executor_trust import load_executor_trust_policy


def _public_key() -> str:
    key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    return base64.b64encode(key).decode("ascii")


def test_executor_trust_policy_requires_explicit_unique_identity_bindings(tmp_path: Path) -> None:
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2

[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["1.0", "1.1"]
public_key_base64 = "{_public_key()}"
''',
        encoding="utf-8",
    )

    policy = load_executor_trust_policy(path)

    assert policy.executors[0].key_id == "prod"
    assert policy.executors[0].executor_versions == ("1.0", "1.1")
    assert len(policy.executors[0].public_key_fingerprint) == 64


def test_executor_trust_policy_rejects_duplicate_key_ids(tmp_path: Path) -> None:
    key = _public_key()
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2

[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{key}"

[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["2"]
public_key_base64 = "{key}"
''',
        encoding="utf-8",
    )

    with pytest.raises(FleetPlanInvalid, match="Duplicate"):
        load_executor_trust_policy(path)


def test_executor_trust_policy_rejects_non_ed25519_key_length(tmp_path: Path) -> None:
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2

[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{base64.b64encode(b"short").decode("ascii")}"
''',
        encoding="utf-8",
    )

    with pytest.raises(FleetPlanInvalid, match="32 bytes"):
        load_executor_trust_policy(path)


@pytest.mark.parametrize(
    "content, message",
    [
        ("version = 1\nexecutors = []\n", "version must be 2"),
        ('version = 2\nexecutors = "bad"\n', "array of tables"),
        ("version = 2\nexecutors = [1]\n", "entries must be tables"),
        ('version = 2\n[[executors]]\nexecutor_name = "runner"\n', "key_id"),
    ],
)
def test_executor_trust_policy_rejects_malformed_policy(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "trust.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match=message):
        load_executor_trust_policy(path)


def test_executor_trust_policy_rejects_invalid_versions_and_entry_shape(tmp_path: Path) -> None:
    key = _public_key()
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = []
public_key_base64 = "{key}"
''',
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="executor_versions"):
        load_executor_trust_policy(path)

    path.write_text(
        f'''version = 2
[[executors]]
key_id = "prod"
executor_versions = ["1"]
public_key_base64 = "{key}"
''',
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="Invalid Fleet executor trust entry"):
        load_executor_trust_policy(path)


def test_executor_trust_policy_rejects_unreadable_and_invalid_base64(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="Invalid Fleet executor trust policy"):
        load_executor_trust_policy(tmp_path / "missing.toml")

    path = tmp_path / "trust.toml"
    path.write_text(
        """version = 2
[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "not base64!"
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="canonical base64"):
        load_executor_trust_policy(path)


def test_verify_executor_attestation_reports_specific_trust_failures(tmp_path: Path) -> None:
    import hashlib

    from oh_goon.integrations.fleet_plan_executor_trust import verify_executor_attestation

    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "prod"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{base64.b64encode(public).decode("ascii")}"
''',
        encoding="utf-8",
    )
    policy = load_executor_trust_policy(path)
    payload = b"payload"
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    fingerprint = hashlib.sha256(public).hexdigest()

    assert (
        verify_executor_attestation(
            policy,
            key_id="unknown",
            fingerprint=fingerprint,
            executor_name="runner",
            executor_version="1",
            issued_at="2026-08-13T18:00:00Z",
            payload=payload,
            signature_base64=signature,
        )
        == "untrusted_key"
    )
    assert (
        verify_executor_attestation(
            policy,
            key_id="prod",
            fingerprint=fingerprint,
            executor_name="other",
            executor_version="1",
            issued_at="2026-08-13T18:00:00Z",
            payload=payload,
            signature_base64=signature,
        )
        == "executor_mismatch"
    )
    assert (
        verify_executor_attestation(
            policy,
            key_id="prod",
            fingerprint="0" * 64,
            executor_name="runner",
            executor_version="1",
            issued_at="2026-08-13T18:00:00Z",
            payload=payload,
            signature_base64=signature,
        )
        == "invalid_signature"
    )
    assert (
        verify_executor_attestation(
            policy,
            key_id="prod",
            fingerprint=fingerprint,
            executor_name="runner",
            executor_version="1",
            issued_at="2026-08-13T18:00:00Z",
            payload=payload,
            signature_base64=base64.b64encode(b"x" * 64).decode(),
        )
        == "invalid_signature"
    )


def test_executor_trust_policy_v2_enforces_key_lifecycle_at_report_issue_time(
    tmp_path: Path,
) -> None:
    import hashlib

    from oh_goon.integrations.fleet_plan_executor_trust import verify_executor_attestation

    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "rotating"
executor_name = "runner"
executor_versions = ["2"]
public_key_base64 = "{base64.b64encode(public).decode("ascii")}"
active_from = "2026-08-13T18:00:00Z"
expires_at = "2026-08-13T21:00:00Z"
revoked_at = "2026-08-13T20:00:00Z"
revocation_mode = "from_time"
''',
        encoding="utf-8",
    )
    policy = load_executor_trust_policy(path)
    payload = b"payload"
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")
    fingerprint = hashlib.sha256(public).hexdigest()

    def state(issued_at: str) -> str:
        return verify_executor_attestation(
            policy,
            key_id="rotating",
            fingerprint=fingerprint,
            executor_name="runner",
            executor_version="2",
            issued_at=issued_at,
            payload=payload,
            signature_base64=signature,
        )

    assert state("2026-08-13T17:59:59Z") == "key_not_yet_valid"
    assert state("2026-08-13T19:59:59Z") == "authenticated"
    assert state("2026-08-13T20:00:00Z") == "key_revoked"


def test_executor_trust_policy_can_revoke_all_historical_reports(tmp_path: Path) -> None:
    import hashlib

    from oh_goon.integrations.fleet_plan_executor_trust import verify_executor_attestation

    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "compromised"
executor_name = "runner"
executor_versions = ["2"]
public_key_base64 = "{base64.b64encode(public).decode("ascii")}"
revoked_at = "2026-08-13T20:00:00Z"
revocation_mode = "all"
''',
        encoding="utf-8",
    )
    policy = load_executor_trust_policy(path)
    payload = b"payload"
    signature = base64.b64encode(private_key.sign(payload)).decode("ascii")

    assert (
        verify_executor_attestation(
            policy,
            key_id="compromised",
            fingerprint=hashlib.sha256(public).hexdigest(),
            executor_name="runner",
            executor_version="2",
            issued_at="2026-08-13T18:00:00Z",
            payload=payload,
            signature_base64=signature,
        )
        == "key_revoked"
    )


def test_executor_trust_policy_rejects_invalid_lifecycle_window(tmp_path: Path) -> None:
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "bad-window"
executor_name = "runner"
executor_versions = ["2"]
public_key_base64 = "{_public_key()}"
active_from = "2026-08-13T21:00:00Z"
expires_at = "2026-08-13T20:00:00Z"
''',
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="active_from must be before expires_at"):
        load_executor_trust_policy(path)


def test_executor_trust_policy_reports_expired_key_and_rejects_bad_lifecycle_fields(
    tmp_path: Path,
) -> None:
    import hashlib

    from oh_goon.integrations.fleet_plan_executor_trust import verify_executor_attestation

    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    encoded = base64.b64encode(public).decode("ascii")
    path = tmp_path / "trust.toml"
    path.write_text(
        f'''version = 2
[[executors]]
key_id = "expiring"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{encoded}"
expires_at = "2026-08-13T19:00:00Z"
''',
        encoding="utf-8",
    )
    policy = load_executor_trust_policy(path)
    payload = b"payload"
    assert (
        verify_executor_attestation(
            policy,
            key_id="expiring",
            fingerprint=hashlib.sha256(public).hexdigest(),
            executor_name="runner",
            executor_version="1",
            issued_at="2026-08-13T19:00:00Z",
            payload=payload,
            signature_base64=base64.b64encode(private_key.sign(payload)).decode("ascii"),
        )
        == "key_expired"
    )

    path.write_text(
        f'''version = 2
[[executors]]
key_id = "bad-mode"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{encoded}"
revocation_mode = "sometimes"
''',
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="revocation_mode"):
        load_executor_trust_policy(path)

    path.write_text(
        f'''version = 2
[[executors]]
key_id = "bad-time"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{encoded}"
active_from = 123
''',
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="active_from must be a UTC timestamp string"):
        load_executor_trust_policy(path)


def test_executor_trust_policy_rejects_bad_timestamp_shapes_and_missing_key(tmp_path: Path) -> None:
    key = _public_key()
    path = tmp_path / "trust.toml"
    for value in ("2026-08-13", "2026-13-99T18:00:00Z"):
        path.write_text(
            f'''version = 2
[[executors]]
key_id = "bad-time"
executor_name = "runner"
executor_versions = ["1"]
public_key_base64 = "{key}"
active_from = "{value}"
''',
            encoding="utf-8",
        )
        with pytest.raises(FleetPlanInvalid, match="UTC second precision"):
            load_executor_trust_policy(path)

    path.write_text(
        """version = 2
[[executors]]
key_id = "missing-key"
executor_name = "runner"
executor_versions = ["1"]
""",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="public_key_base64 must be a string"):
        load_executor_trust_policy(path)
