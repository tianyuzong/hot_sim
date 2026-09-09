"""Execution lifecycle shared by synchronous and background computations."""

from contextlib import nullcontext
from typing import Callable, ContextManager, Protocol

ProgressCallback = Callable[[str, float | None], None]


class ExecutionMonitor(Protocol):
    def report(self, stage: str, progress: float | None = None) -> None: ...
    def publishing(self) -> ContextManager[None]: ...


class UnmonitoredExecution:
    def report(self, stage: str, progress: float | None = None) -> None:
        pass

    def publishing(self) -> ContextManager[None]:
        return nullcontext()
