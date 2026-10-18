from __future__ import annotations

from collections.abc import Callable
from typing import Literal

StepOutputObserver = Callable[[str, int, Literal["stdout", "stderr"], str], None]
