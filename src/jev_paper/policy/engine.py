"""Map a judgment, or the book itself, onto one action."""

from __future__ import annotations

from jev_paper.config import PolicyConfig
from jev_paper.types import Action, ActionDecision, Judgment, Snapshot


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def from_judgment(self, snapshot: Snapshot, judgment: Judgment, *, gated: bool) -> ActionDecision:
        if gated and not self._confident(judgment):
            decision = self.code_only(snapshot)
            return ActionDecision(
                decision.action,
                "confidence gate, " + decision.reason,
                "code_only",
                decision.skew_bps,
                decision.spread_multiplier,
            )
        return self._from_jev(snapshot, judgment)

    def code_only(self, snapshot: Snapshot) -> ActionDecision:
        if snapshot.warmup or snapshot.mid is None:
            return ActionDecision(Action.STAND_DOWN, "warmup", "code_only")
        vol_ratio = snapshot.vol_vs_24h
        if vol_ratio is not None and vol_ratio >= self.config.code_vol_ratio_stand_down:
            return ActionDecision(Action.STAND_DOWN, "realized vol versus trailing day", "code_only")
        if snapshot.spread_bps is not None and snapshot.spread_bps >= self.config.code_spread_bps_pull:
            return ActionDecision(Action.PULL_QUOTES, "book spread is wide", "code_only")
        skew = 0.0
        imbalance = snapshot.imbalance
        if imbalance is not None and abs(imbalance) >= self.config.code_imbalance_skew:
            sign = 1.0 if imbalance > 0 else -1.0
            skew = sign * self.config.direction_skew_bps
        return ActionDecision(Action.QUOTE_BOTH_SIDES, "code book rules", "code_only", skew_bps=skew)

    def _confident(self, judgment: Judgment) -> bool:
        floor = self.config.min_choice_confidence
        if judgment.regime is None or judgment.direction is None:
            return False
        return judgment.regime.confidence >= floor and judgment.direction.confidence >= floor

    def _from_jev(self, snapshot: Snapshot, judgment: Judgment) -> ActionDecision:
        regime = judgment.regime.choice if judgment.regime else ""
        if regime == "crisis":
            return ActionDecision(Action.KILL, "regime crisis", "jev")
        toxic = judgment.toxic_flow
        if toxic is not None and toxic >= self.config.toxic_flow_pull:
            return ActionDecision(Action.PULL_QUOTES, "toxic flow", "jev")
        quote = judgment.quote_environment
        if quote is not None and quote.score <= self.config.quote_env_stand_down_max:
            return ActionDecision(Action.STAND_DOWN, "quote environment", "jev")

        multiplier = 1.0
        action = Action.QUOTE_BOTH_SIDES
        reason = "quote both sides"
        stressed = judgment.liquidity_stressed
        if stressed is not None and stressed >= self.config.liquidity_stress_widen:
            action = Action.WIDEN
            multiplier = self.config.widen_multiplier
            reason = "liquidity stressed"
        elif quote is not None and quote.score <= self.config.quote_env_wide_max:
            action = Action.QUOTE_WIDE
            multiplier = self.config.wide_multiplier
            reason = "marginal quote environment"

        skew = 0.0
        if judgment.direction is not None and judgment.direction.choice == "up":
            skew += self.config.direction_skew_bps
        elif judgment.direction is not None and judgment.direction.choice == "down":
            skew -= self.config.direction_skew_bps

        pressure = judgment.inventory_pressure.score if judgment.inventory_pressure else None
        if pressure is not None and pressure >= self.config.inventory_reduce_min:
            action = Action.WIDEN
            multiplier = max(multiplier, self.config.widen_multiplier)
            reason = "reduce inventory"
            skew += _inventory_skew(snapshot.inventory, self.config.inventory_skew_bps)
        elif pressure is not None and pressure >= self.config.inventory_skew_hard_min:
            skew += _inventory_skew(snapshot.inventory, self.config.inventory_skew_bps)
            if reason == "quote both sides":
                reason = "skew inventory"
        return ActionDecision(action, reason, "jev", skew_bps=skew, spread_multiplier=multiplier)


def _inventory_skew(inventory: float, bps: float) -> float:
    if inventory > 0:
        return -bps
    if inventory < 0:
        return bps
    return 0.0
