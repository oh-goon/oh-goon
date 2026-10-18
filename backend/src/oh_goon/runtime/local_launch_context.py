from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, MutableMapping

LOCAL_LAUNCH_ID_ENV = "OH_GOON_LAUNCH_ID"
LOCAL_LAUNCH_OWNER_GENERATION_ENV = "OH_GOON_LAUNCH_OWNER_GENERATION"
LOCAL_LAUNCH_BACKEND_GENERATION_ENV = "OH_GOON_LAUNCH_BACKEND_GENERATION"


@dataclass(frozen=True, slots=True)
class LocalLaunchRuntimeContext:
    """Generation-bound launch identity propagated to owned local runtime children."""

    launch_id: str
    owner_generation: int
    backend_generation: int

    def __post_init__(self) -> None:
        if not self.launch_id:
            raise ValueError("local launch context requires launch_id")
        if self.owner_generation < 1:
            raise ValueError("local launch owner generation must be >= 1")
        if self.backend_generation < 1:
            raise ValueError("local launch backend generation must be >= 1")

    def install(self, environ: MutableMapping[str, str] | None = None) -> None:
        target = os.environ if environ is None else environ
        target[LOCAL_LAUNCH_ID_ENV] = self.launch_id
        target[LOCAL_LAUNCH_OWNER_GENERATION_ENV] = str(self.owner_generation)
        target[LOCAL_LAUNCH_BACKEND_GENERATION_ENV] = str(self.backend_generation)

    def clear(self, environ: MutableMapping[str, str] | None = None) -> None:
        target = os.environ if environ is None else environ
        expected = {
            LOCAL_LAUNCH_ID_ENV: self.launch_id,
            LOCAL_LAUNCH_OWNER_GENERATION_ENV: str(self.owner_generation),
            LOCAL_LAUNCH_BACKEND_GENERATION_ENV: str(self.backend_generation),
        }
        for name, value in expected.items():
            if target.get(name) == value:
                target.pop(name, None)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> LocalLaunchRuntimeContext | None:
        source = os.environ if environ is None else environ
        values = {
            LOCAL_LAUNCH_ID_ENV: source.get(LOCAL_LAUNCH_ID_ENV),
            LOCAL_LAUNCH_OWNER_GENERATION_ENV: source.get(LOCAL_LAUNCH_OWNER_GENERATION_ENV),
            LOCAL_LAUNCH_BACKEND_GENERATION_ENV: source.get(LOCAL_LAUNCH_BACKEND_GENERATION_ENV),
        }
        if all(value is None for value in values.values()):
            return None
        missing = [name for name, value in values.items() if value is None]
        if missing:
            raise ValueError(
                f"incomplete local launch context: missing {', '.join(sorted(missing))}"
            )
        launch_id = values[LOCAL_LAUNCH_ID_ENV]
        owner = values[LOCAL_LAUNCH_OWNER_GENERATION_ENV]
        backend = values[LOCAL_LAUNCH_BACKEND_GENERATION_ENV]
        assert launch_id is not None and owner is not None and backend is not None
        try:
            owner_generation = int(owner)
            backend_generation = int(backend)
        except ValueError as exc:
            raise ValueError("local launch generations must be integers") from exc
        return cls(
            launch_id=launch_id,
            owner_generation=owner_generation,
            backend_generation=backend_generation,
        )
