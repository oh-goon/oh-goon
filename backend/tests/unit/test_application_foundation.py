from __future__ import annotations

import pytest

from oh_goon._application_foundation import _require_complete_runner_wiring
from oh_goon.core.capability_model import CapabilityRunnerKind


def test_production_runner_wiring_accepts_exact_registered_families() -> None:
    _require_complete_runner_wiring({kind: object() for kind in CapabilityRunnerKind})


def test_production_runner_wiring_rejects_missing_registered_family() -> None:
    runners = {kind: object() for kind in CapabilityRunnerKind}
    runners.pop(CapabilityRunnerKind.FLEET_PLAN)

    with pytest.raises(AssertionError, match=r"missing=\['FLEET_PLAN'\]"):
        _require_complete_runner_wiring(runners)
