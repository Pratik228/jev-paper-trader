"""Reservation price and spread. Spread is clamped with config limits."""

from __future__ import annotations

import math

from jev_paper.config import PricingConfig, RiskConfig
from jev_paper.types import Action, ActionDecision, Quote, Snapshot


def quotes_for(
    pricer: str,
    snapshot: Snapshot,
    decision: ActionDecision,
    pricing: PricingConfig,
    risk: RiskConfig,
) -> tuple[Quote | None, Quote | None]:
    if decision.action in {Action.HOLD, Action.PULL_QUOTES, Action.STAND_DOWN, Action.KILL}:
        return None, None
    if snapshot.mid is None or snapshot.mid <= 0:
        return None, None
    if pricer == "fixed":
        bid, ask = _fixed(snapshot, decision, pricing)
    else:
        bid, ask = _avellaneda(snapshot, decision, pricing)
    return _sized(bid, ask, snapshot.mid, snapshot.inventory, pricing, risk)


def _avellaneda(
    snapshot: Snapshot,
    decision: ActionDecision,
    pricing: PricingConfig,
) -> tuple[float, float]:
    mid = snapshot.mid or 0.0
    return_vol = snapshot.realized_vol_5m if snapshot.realized_vol_5m else pricing.sigma_floor
    sigma = max(mid * return_vol, pricing.sigma_floor)
    tau = pricing.horizon_s
    gamma = pricing.gamma
    variance_term = gamma * sigma * sigma * tau
    reservation = mid - snapshot.inventory * variance_term
    reservation *= 1.0 + decision.skew_bps / 10_000.0
    indifference = (2.0 / gamma) * math.log(1.0 + gamma / pricing.k)
    half = 0.5 * (variance_term + indifference) * decision.spread_multiplier
    return _clamp_around(reservation, half, mid, pricing)


def _fixed(
    snapshot: Snapshot,
    decision: ActionDecision,
    pricing: PricingConfig,
) -> tuple[float, float]:
    mid = snapshot.mid or 0.0
    half = mid * pricing.baseline_half_spread_bps / 10_000.0 * decision.spread_multiplier
    shift_bps = -snapshot.inventory * pricing.baseline_skew_bps_per_btc + decision.skew_bps
    reservation = mid * (1.0 + shift_bps / 10_000.0)
    return _clamp_around(reservation, half, mid, pricing)


def _clamp_around(
    reservation: float,
    half: float,
    mid: float,
    pricing: PricingConfig,
) -> tuple[float, float]:
    min_half = mid * pricing.min_half_spread_bps / 10_000.0
    max_half = mid * pricing.max_half_spread_bps / 10_000.0
    half = min(max(half, min_half), max_half)
    bid = reservation - half
    ask = reservation + half
    if bid <= 0 or bid >= ask:
        bid = mid - min_half
        ask = mid + min_half
    return bid, ask


def _sized(
    bid: float,
    ask: float,
    mid: float,
    inventory: float,
    pricing: PricingConfig,
    risk: RiskConfig,
) -> tuple[Quote | None, Quote | None]:
    del pricing
    room = risk.max_position_usd - abs(inventory * mid)
    bid_qty = _qty(bid, risk, room if inventory >= 0 else risk.max_position_usd)
    ask_qty = _qty(ask, risk, room if inventory <= 0 else risk.max_position_usd)
    bid_quote = Quote(bid, bid_qty) if bid_qty > 0 else None
    ask_quote = Quote(ask, ask_qty) if ask_qty > 0 else None
    return bid_quote, ask_quote


def _qty(price: float, risk: RiskConfig, room_usd: float) -> float:
    if price <= 0 or room_usd <= 0:
        return 0.0
    capped = min(risk.quote_size, risk.max_order_usd / price, room_usd / price)
    return max(0.0, capped)
