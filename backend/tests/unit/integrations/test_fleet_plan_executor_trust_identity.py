from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.integrations.fleet_plan_executor_trust import (
    executor_trust_policy_id,
    export_executor_trust_policy_snapshot,
    load_executor_trust_policy,
)


def test_trust_policy_identity_is_semantic_not_toml_order(tmp_path: Path) -> None:
    keys = []
    for name in ("a", "b"):
        public = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        keys.append((name, base64.b64encode(public).decode("ascii")))
    a = tmp_path / "a.toml"
    b = tmp_path / "b.toml"
    a.write_text(
        f'''version = 2
[[executors]]
key_id = "a"
executor_name = "runner-a"
executor_versions = ["2", "1"]
public_key_base64 = "{keys[0][1]}"
[[executors]]
key_id = "b"
executor_name = "runner-b"
executor_versions = ["1"]
public_key_base64 = "{keys[1][1]}"
''',
        encoding="utf-8",
    )
    b.write_text(
        f'''version = 2
[[executors]]
key_id = "b"
executor_name = "runner-b"
executor_versions = ["1"]
public_key_base64 = "{keys[1][1]}"
[[executors]]
key_id = "a"
executor_name = "runner-a"
executor_versions = ["1", "2"]
public_key_base64 = "{keys[0][1]}"
''',
        encoding="utf-8",
    )
    first = load_executor_trust_policy(a)
    second = load_executor_trust_policy(b)
    assert executor_trust_policy_id(first) == executor_trust_policy_id(second)
    snapshot = export_executor_trust_policy_snapshot(first)
    assert snapshot.snapshot.policy_id == executor_trust_policy_id(first)
    assert [item.key_id for item in snapshot.snapshot.policy.executors] == ["a", "b"]
    assert snapshot.snapshot.policy.executors[0].executor_versions == ("1", "2")
