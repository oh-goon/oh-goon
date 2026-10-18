from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_run import SealedAttemptOutputs


def _attempt(root: Path, authority: object) -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(
            root=root,
            workspace=root / "workspace",
            cache=root / "cache",
        ),
        _output_publication_authority=authority,
    )


def _paths(root: Path) -> None:
    (root / "workspace").mkdir(parents=True)
    (root / "cache").mkdir()


def test_sealed_attempt_outputs_cannot_be_forged_from_matching_paths(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    _paths(root)
    attempt = _attempt(root, object())
    forged = SealedAttemptOutputs(
        attempt_root=root.resolve(),
        workspace=(root / "workspace").resolve(),
        cache=(root / "cache").resolve(),
        _authority=object(),
    )

    with pytest.raises(FleetPlanInvalid, match="does not match physical attempt"):
        forged.prove_for(attempt)


def test_sealed_attempt_outputs_reject_cross_attempt_reuse_with_identical_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "attempt"
    _paths(root)
    first = _attempt(root, object())
    second = _attempt(root, object())
    legitimate = SealedAttemptOutputs(
        attempt_root=root.resolve(),
        workspace=(root / "workspace").resolve(),
        cache=(root / "cache").resolve(),
        _authority=first._output_publication_authority,
    )

    legitimate.prove_for(first)
    with pytest.raises(FleetPlanInvalid, match="does not match physical attempt"):
        legitimate.prove_for(second)


def test_sealed_attempt_outputs_reject_path_substitution(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    _paths(root)
    authority = object()
    attempt = _attempt(root, authority)
    substituted = SealedAttemptOutputs(
        attempt_root=root.resolve(),
        workspace=(tmp_path / "other-workspace").resolve(),
        cache=(root / "cache").resolve(),
        _authority=authority,
    )

    with pytest.raises(FleetPlanInvalid, match="does not match physical attempt"):
        substituted.prove_for(attempt)


def test_failed_sealing_mints_no_authority_and_retry_mints_fresh_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.integrations.fleet_plan_run as run_module
    from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt

    root = tmp_path / "attempt"
    _paths(root)
    authority = object()
    attempt = _attempt(root, authority)
    minted: list[object] = []
    fail_validation = True

    class _OwnedTreeProof:
        @classmethod
        def from_root(cls, _root: Path) -> "_OwnedTreeProof":
            return cls()

        def require_owned_tree(self) -> None:
            if fail_validation:
                raise FleetPlanInvalid("ownership validation failed")

    real_seal_type = run_module.SealedAttemptOutputs

    def record_mint(**kwargs: object) -> SealedAttemptOutputs:
        token = real_seal_type(**kwargs)
        minted.append(token)
        return token

    monkeypatch.setattr(run_module, "_materialize_generated_runtime_links", lambda _attempt: None)
    monkeypatch.setattr(run_module, "RepositoryPathSpace", _OwnedTreeProof)
    monkeypatch.setattr(run_module, "SealedAttemptOutputs", record_mint)

    with pytest.raises(FleetPlanInvalid, match="ownership validation failed"):
        FleetPlanStepAttempt.seal_owned_outputs(attempt)
    assert minted == []

    fail_validation = False
    token = FleetPlanStepAttempt.seal_owned_outputs(attempt)
    assert minted == [token]
    token.prove_for(attempt)
