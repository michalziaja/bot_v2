"""Step protocol + StepResult — kontrakt każdego pluginu."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from v2.runner.context import Context


@dataclass
class StepResult:
    status: str                       # ok / skipped / failed / aborted_by_gate
    payload: dict = field(default_factory=dict)
    error:   str | None = None
    duration_ms: int = 0

    @classmethod
    def ok(cls, **payload):      return cls("ok", payload=payload)
    @classmethod
    def skipped(cls, reason: str, **payload): return cls("skipped", payload={"reason": reason, **payload})
    @classmethod
    def failed(cls, error: str, **payload):   return cls("failed", payload=payload, error=error)


class Step(Protocol):
    """Każdy krok implementuje ten protokół. Wystarczy klasa z:
       - atrybutem klasowym `name` (np. 'system.wake')
       - atrybutem klasowym `requires_screen` (str | None)
       - metodą `run(ctx, params) -> StepResult`.
    Rejestrujemy przez `steps.registry.register`."""
    name: str
    requires_screen: str | None
    def run(self, ctx: "Context", params: dict) -> StepResult: ...
