from __future__ import annotations

from dataclasses import replace

from jev_paper.config import PricingConfig, RiskConfig
from jev_paper.pricing.avellaneda import quotes_for
from jev_paper.types import Action, ActionDecision, Snapshot


def _snap(inventory: float, vol: float = 0.001) -> Snapshot:
    return Snapshot(
        block_id=1,
        ts_ms=1,
        mid=100.0,
        microprice=100.0,
        spread_bps=2.0,
        depth_bid=[1.0],
        depth_ask=[1.0],
        imbalance=0.0,
        ret_1m=0.0,
        ret_5m=0.0,
        ret_30m=0.0,
        aggressive_buy_ratio=None,
        realized_vol_5m=vol,
        realized_vol_30m=vol,
        vol_vs_24h=None,
        inventory=inventory,
        unrealized_pnl=0.0,
        drawdown=0.0,
        latency_ms_last10=[],
        warmup=False,
    )


def test_long_inventory_lowers_the_bid() -> None:
    decision = ActionDecision(Action.QUOTE_BOTH_SIDES, "quote", "jev")
    pricing = PricingConfig(min_half_spread_bps=0.1, max_half_spread_bps=500)
    risk = RiskConfig(quote_size=0.01, max_order_usd=10_000, max_position_usd=10_000)
    flat, _ask = quotes_for("avellaneda", _snap(0.0), decision, pricing, risk)
    long, _ask = quotes_for("avellaneda", _snap(5.0), decision, pricing, risk)
    assert flat is not None and long is not None
    assert long.price < flat.price


def test_hold_quotes_nothing_new() -> None:
    decision = ActionDecision(Action.HOLD, "stale", "hold")
    bid, ask = quotes_for("fixed", _snap(0.0), decision, PricingConfig(), RiskConfig())
    assert bid is None and ask is None


def test_order_notional_is_capped() -> None:
    decision = ActionDecision(Action.QUOTE_BOTH_SIDES, "quote", "code_only")
    risk = RiskConfig(quote_size=5.0, max_order_usd=50.0, max_position_usd=500.0)
    bid, ask = quotes_for("fixed", _snap(0.0), decision, PricingConfig(), risk)
    assert bid is not None and ask is not None
    assert bid.price * bid.qty <= 50.0 + 1e-9
    assert ask.qty < 5.0


def test_replace_helper_keeps_snapshot_frozen() -> None:
    snap = _snap(0.0)
    assert replace(snap, inventory=1.0).inventory == 1.0
    assert snap.inventory == 0.0
