"""The paper strategies. They share snapshots, fees, and risk limits.

`code_only` is the control for the model slot: same Avellaneda pricer and risk
engine as the Jev arms, but every decision comes from the code book rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategySpec:
    name: str
    uses_jev: bool
    gated: bool
    pricer: str


SPECS: tuple[StrategySpec, ...] = (
    StrategySpec("jev_gated", True, True, "avellaneda"),
    StrategySpec("jev_ungated", True, False, "avellaneda"),
    StrategySpec("code_only", False, False, "avellaneda"),
    StrategySpec("baseline", False, False, "fixed"),
)
