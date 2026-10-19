from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchEndpointObservation,
    LaunchFactState,
    LaunchFailure,
    LaunchIdentity,
    LaunchIntent,
    LaunchLifecycle,
    LaunchLifecycleState,
    LaunchManifest,
    LaunchObservedState,
    LaunchProcessObservation,
    LaunchServiceIntent,
    LaunchServiceObservation,
    ObservedFact,
    _json_compatible,
)
from oh_goon.core.process_birth import (
    ProcessBirthIdentity,
    observe_process_birth,
    observe_process_liveness,
    process_birth_matches,
)

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)


def _fact(value, *, owner: int = 2, service: int | None = 3):
    return ObservedFact(
        value=value,
        asserted_by="runtime",
        observed_at=NOW,
        owner_generation=owner,
        service_generation=service,
    )


def _birth(pid: int = 41) -> ProcessBirthIdentity:
    return ProcessBirthIdentity(pid=pid, boot_id="boot-a", start_ticks=99)


def test_launch_leaf_contracts_fail_closed() -> None:
    with pytest.raises(ValueError, match="asserted_by"):
        ObservedFact(value=1, asserted_by="", observed_at=NOW, owner_generation=1)
    with pytest.raises(ValueError, match="service_generation"):
        ObservedFact(
            value=1, asserted_by="x", observed_at=NOW, owner_generation=1, service_generation=0
        )
    for service, host, port in (
        ("", "127.0.0.1", 1),
        ("api", "", 1),
        ("api", "127.0.0.1", 0),
        ("api", "127.0.0.1", 65536),
    ):
        with pytest.raises(ValueError):
            LaunchEndpointIntent(service=service, host=host, port=port)
    with pytest.raises(ValueError, match="service name"):
        LaunchServiceIntent(service="", command=("x",))
    with pytest.raises(ValueError, match="command"):
        LaunchServiceIntent(service="api", command=())
    with pytest.raises(ValueError, match="same service"):
        LaunchServiceIntent(
            service="api",
            command=("x",),
            endpoint=LaunchEndpointIntent(service="other", host="127.0.0.1", port=1),
        )
    with pytest.raises(ValueError, match="profile"):
        LaunchIntent(profile="", services=())
    one = LaunchServiceIntent(service="api", command=("x",))
    with pytest.raises(ValueError, match="unique"):
        LaunchIntent(profile="dev", services=(one, one))


def test_process_and_endpoint_observations_reject_stale_or_malformed_facts() -> None:
    with pytest.raises(ValueError, match="positive"):
        LaunchProcessObservation(pid=_fact(0))
    pid = _fact(41)
    with pytest.raises(ValueError, match="pid must match"):
        LaunchProcessObservation(pid=pid, birth_identity=_fact(_birth(42)))
    with pytest.raises(ValueError, match="owner generation"):
        LaunchProcessObservation(pid=pid, birth_identity=_fact(_birth(), owner=9))
    with pytest.raises(ValueError, match="service generation"):
        LaunchProcessObservation(pid=pid, birth_identity=_fact(_birth(), service=4))
    with pytest.raises(ValueError, match="host"):
        LaunchEndpointObservation(host=_fact(""), port=_fact(1))
    for port in (0, 65536):
        with pytest.raises(ValueError, match="port"):
            LaunchEndpointObservation(host=_fact("127.0.0.1"), port=_fact(port))
    with pytest.raises(ValueError, match="owner generation"):
        LaunchEndpointObservation(host=_fact("127.0.0.1"), port=_fact(1, owner=9))
    with pytest.raises(ValueError, match="service generation"):
        LaunchEndpointObservation(host=_fact("127.0.0.1"), port=_fact(1, service=4))


def test_service_observation_and_observed_state_validate_all_generation_edges() -> None:
    with pytest.raises(ValueError, match="requires service"):
        LaunchServiceObservation(service="", service_generation=1)
    with pytest.raises(ValueError, match=">= 1"):
        LaunchServiceObservation(service="api", service_generation=0)
    with pytest.raises(ValueError, match="process observation"):
        LaunchServiceObservation(
            service="api",
            service_generation=2,
            process=LaunchProcessObservation(pid=_fact(41, service=3)),
        )
    endpoint = LaunchEndpointObservation(
        host=_fact("127.0.0.1", service=4), port=_fact(1, service=4)
    )
    with pytest.raises(ValueError, match="endpoint observation"):
        LaunchServiceObservation(service="api", service_generation=3, bound_endpoint=endpoint)
    for field in ("startup", "readiness", "health"):
        with pytest.raises(ValueError, match=field):
            LaunchServiceObservation(
                service="api",
                service_generation=3,
                **{field: _fact(LaunchFactState.SATISFIED, service=4)},
            )
    service = LaunchServiceObservation(service="api", service_generation=3)
    with pytest.raises(ValueError, match="unknown service state"):
        service.state("other")
    assert service.state("startup") is LaunchFactState.UNKNOWN
    owner_pid = LaunchProcessObservation(pid=_fact(41, service=3))
    with pytest.raises(ValueError, match="owner process"):
        LaunchObservedState(owner_process=owner_pid)
    dup = LaunchServiceObservation(service="api", service_generation=1)
    with pytest.raises(ValueError, match="unique"):
        LaunchObservedState(services=(dup, dup))


def test_manifest_identity_lifecycle_failure_and_schema_contracts() -> None:
    for kwargs in (
        {"launch_id": "", "workspace": "/w", "oh_goon_version": "1", "owner_generation": 1},
        {"launch_id": "x", "workspace": "", "oh_goon_version": "1", "owner_generation": 1},
        {"launch_id": "x", "workspace": "/w", "oh_goon_version": "", "owner_generation": 1},
    ):
        with pytest.raises(ValueError, match="requires"):
            LaunchIdentity(**kwargs)
    with pytest.raises(ValueError, match="owner_generation"):
        LaunchIdentity(launch_id="x", workspace="/w", oh_goon_version="1", owner_generation=0)
    with pytest.raises(ValueError, match="timezone-aware"):
        LaunchLifecycle(state=LaunchLifecycleState.READY, ready_at=datetime(2026, 1, 1))
    for field in ("kind", "code", "owner", "reason"):
        values = {"kind": "runtime", "code": "X", "owner": "o", "reason": "r", "retryable": False}
        values[field] = ""
        with pytest.raises(ValueError, match="required"):
            LaunchFailure(**values)
    ident = LaunchIdentity(launch_id="x", workspace="/w", oh_goon_version="1", owner_generation=2)
    intent = LaunchIntent(profile="dev", services=())
    with pytest.raises(ValueError, match="schema"):
        LaunchManifest(
            identity=ident,
            intent=intent,
            observed=LaunchObservedState(),
            lifecycle=LaunchLifecycle(state=LaunchLifecycleState.RESOLVING),
            schema="bad",
        )
    with pytest.raises(ValueError, match="kind"):
        LaunchManifest(
            identity=ident,
            intent=intent,
            observed=LaunchObservedState(),
            lifecycle=LaunchLifecycle(state=LaunchLifecycleState.RESOLVING),
            kind="bad",
        )


def test_manifest_rejects_owner_generation_on_owner_and_service_facts() -> None:
    ident = LaunchIdentity(launch_id="x", workspace="/w", oh_goon_version="1", owner_generation=2)
    intent = LaunchIntent(
        profile="dev", services=(LaunchServiceIntent(service="api", command=("run",)),)
    )
    owner = LaunchProcessObservation(pid=_fact(40, owner=3, service=None))
    with pytest.raises(ValueError, match="owner generation"):
        LaunchManifest(
            identity=ident,
            intent=intent,
            observed=LaunchObservedState(owner_process=owner),
            lifecycle=LaunchLifecycle(state=LaunchLifecycleState.STARTING),
        )
    service = LaunchServiceObservation(
        service="api",
        service_generation=3,
        health=_fact(LaunchFactState.SATISFIED, owner=3, service=3),
    )
    with pytest.raises(ValueError, match="another launch owner"):
        LaunchManifest(
            identity=ident,
            intent=intent,
            observed=LaunchObservedState(services=(service,)),
            lifecycle=LaunchLifecycle(state=LaunchLifecycleState.READY),
        )


def test_json_projection_and_intent_identity_are_value_semantic() -> None:
    endpoint = LaunchEndpointIntent(service="api", host="127.0.0.1", port=8080)
    a = LaunchIntent(
        profile="dev",
        services=(
            LaunchServiceIntent(service="api", command=("run",), required=False, endpoint=endpoint),
        ),
    )
    b = LaunchIntent(
        profile="dev",
        services=(
            LaunchServiceIntent(service="api", command=("run",), required=False, endpoint=endpoint),
        ),
    )
    assert a.identity() == b.identity()
    assert _json_compatible({"when": NOW, "state": LaunchFactState.SATISFIED, "xs": (1, 2)}) == {
        "when": NOW.isoformat(),
        "state": "satisfied",
        "xs": [1, 2],
    }


def _proc_tree(root: Path, pid: int, *, stat: str, boot: str = "boot-a") -> None:
    (root / "sys/kernel/random").mkdir(parents=True, exist_ok=True)
    (root / "sys/kernel/random/boot_id").write_text(boot, encoding="utf-8")
    (root / str(pid)).mkdir(parents=True, exist_ok=True)
    (root / str(pid) / "stat").write_text(stat, encoding="utf-8")


def _stat(pid: int, *, state: str = "S", start: str = "99") -> str:
    # field 3 begins after comm; field 22 is tail index 19.
    tail = [state] + ["0"] * 18 + [start] + ["0"] * 4
    return f"{pid} (worker name) " + " ".join(tail)


def test_process_birth_payload_and_procfs_observation_matrix(tmp_path: Path) -> None:
    valid = _birth()
    assert ProcessBirthIdentity.from_payload(valid.to_payload()) == valid
    for payload in (
        None,
        {},
        {**valid.to_payload(), "provider": 1},
        {**valid.to_payload(), "pid": True},
        {**valid.to_payload(), "boot_id": 1},
        {**valid.to_payload(), "start_ticks": True},
    ):
        with pytest.raises(ValueError):
            ProcessBirthIdentity.from_payload(payload)
    for kwargs in (
        {"pid": 0, "boot_id": "x", "start_ticks": 0},
        {"pid": 1, "boot_id": "", "start_ticks": 0},
        {"pid": 1, "boot_id": "x", "start_ticks": -1},
        {"pid": 1, "boot_id": "x", "start_ticks": 0, "provider": "bad"},
    ):
        with pytest.raises(ValueError):
            ProcessBirthIdentity(**kwargs)
    with pytest.raises(ValueError, match="positive"):
        observe_process_birth(0, proc_root=tmp_path)
    assert observe_process_birth(9, proc_root=tmp_path) is None
    _proc_tree(tmp_path, 9, stat=_stat(9))
    assert observe_process_birth(9, proc_root=tmp_path) == ProcessBirthIdentity(
        pid=9, boot_id="boot-a", start_ticks=99
    )
    assert process_birth_matches(
        ProcessBirthIdentity(pid=9, boot_id="boot-a", start_ticks=99), proc_root=tmp_path
    )


def test_process_birth_and_liveness_reject_malformed_procfs(tmp_path: Path) -> None:
    cases = [
        "bad",
        "9 worker",
        "x (worker) " + " ".join(["S"] + ["0"] * 22),
        _stat(9, start="bad"),
        _stat(10),
    ]
    for i, content in enumerate(cases):
        root = tmp_path / str(i)
        _proc_tree(root, 9, stat=content)
        assert observe_process_birth(9, proc_root=root) is None
    empty_boot = tmp_path / "empty"
    _proc_tree(empty_boot, 9, stat=_stat(9), boot="")
    assert observe_process_birth(9, proc_root=empty_boot) is None
    with pytest.raises(ValueError, match="positive"):
        observe_process_liveness(0, proc_root=tmp_path)
    assert observe_process_liveness(9, proc_root=tmp_path / "absent") is None
    root = tmp_path / "live"
    _proc_tree(root, 9, stat=_stat(9, state="S"))
    assert observe_process_liveness(9, proc_root=root) is True
    (root / "9" / "stat").write_text(_stat(9, state="Z"), encoding="utf-8")
    assert observe_process_liveness(9, proc_root=root) is False
    (root / "9" / "stat").unlink()
    assert observe_process_liveness(9, proc_root=root) is False
