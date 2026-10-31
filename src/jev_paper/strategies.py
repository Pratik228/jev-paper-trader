"""The three paper strategies. They share snapshots, fees, and risk limits."""

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
    StrategySpec("baseline", False, False, "fixed"),
)
