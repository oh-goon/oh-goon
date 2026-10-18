from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns

from oh_goon.core.errors import InvalidRequest, StateConflict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal


@dataclass(frozen=True, slots=True)
class JournalReadProfile:
    revision: int
    cold_milliseconds: float
    warm_milliseconds: tuple[float, ...]

    @property
    def warm_average_milliseconds(self) -> float:
        if not self.warm_milliseconds:
            return 0.0
        return sum(self.warm_milliseconds) / len(self.warm_milliseconds)

    def render(self) -> str:
        warm = ", ".join(f"{value:.3f}" for value in self.warm_milliseconds) or "n/a"
        return "\n".join(
            (
                f"Journal revision: {self.revision}",
                f"Cold verified read: {self.cold_milliseconds:.3f} ms",
                f"Warm cached reads:  {warm} ms",
                f"Warm average:       {self.warm_average_milliseconds:.3f} ms",
                "",
                "The cache is disposable derived state. Journal facts and hash verification remain authoritative.",
            )
        )


class JournalDiagnosticsService:
    """Developer-facing Journal diagnostics without exposing LocalDatabase to entrypoints."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def profile_reads(self, *, samples: int = 5) -> JournalReadProfile:
        if samples < 1 or samples > 100:
            raise InvalidRequest("samples must be between 1 and 100")
        journal = Journal(self._database)
        started = perf_counter_ns()
        first = journal.read_view()
        cold = _elapsed_milliseconds(started)
        warm: list[float] = []
        for _ in range(samples):
            started = perf_counter_ns()
            view = journal.read_view()
            if view.revision != first.revision:
                raise StateConflict("Journal changed while profiling read performance")
            warm.append(_elapsed_milliseconds(started))
        return JournalReadProfile(
            revision=first.revision,
            cold_milliseconds=cold,
            warm_milliseconds=tuple(warm),
        )


def _elapsed_milliseconds(started_ns: int) -> float:
    return (perf_counter_ns() - started_ns) / 1_000_000
