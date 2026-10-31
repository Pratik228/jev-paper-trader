"""Shared records. Market data, snapshots, judgments, and actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


class Action(str, Enum):
    KILL = "KILL"
    PULL_QUOTES = "PULL_QUOTES"
    WIDEN = "WIDEN"
    QUOTE_BOTH_SIDES = "QUOTE_BOTH_SIDES"
    QUOTE_WIDE = "QUOTE_WIDE"
    STAND_DOWN = "STAND_DOWN"
    HOLD = "HOLD"


@dataclass(frozen=True)
class BookLevel:
    price: float
    qty: float


@dataclass(frozen=True)
class BookEvent:
    ts_ms: int
    venue: str
    pair: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    recv_ts_ms: int | None = None
    kind: Literal["book"] = "book"


@dataclass(frozen=True)
class TradeEvent:
    ts_ms: int
    venue: str
    pair: str
    price: float
    qty: float
    aggressor: Literal["buy", "sell"]
    recv_ts_ms: int | None = None
    kind: Literal["trade"] = "trade"


Event = BookEvent | TradeEvent


@dataclass(frozen=True)
class AccountView:
    inventory: float
    unrealized_pnl: float
    drawdown: float


@dataclass(frozen=True)
class Snapshot:
    block_id: int
    ts_ms: int
    mid: float | None
    microprice: float | None
    spread_bps: float | None
    depth_bid: list[float]
    depth_ask: list[float]
    imbalance: float | None
    ret_1m: float | None
    ret_5m: float | None
    ret_30m: float | None
    aggressive_buy_ratio: float | None
    realized_vol_5m: float | None
    realized_vol_30m: float | None
    vol_vs_24h: float | None
    inventory: float
    unrealized_pnl: float
    drawdown: float
    latency_ms_last10: list[float]
    warmup: bool

    def compact(self) -> dict[str, Any]:
        """Numeric state for Jev. No thresholds, no instructions."""
        return asdict(self)


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    label: str
    confidence: float
    probabilities: dict[str, float]


@dataclass
class Judgment:
    regime: ChoiceAnswer | None
    direction: ChoiceAnswer | None
    toxic_flow: float | None
    liquidity_stressed: float | None
    quote_environment: ScoreAnswer | None
    inventory_pressure: ScoreAnswer | None
    model_version: str
    latency_ms: float
    stale: bool
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def choice(value: ChoiceAnswer | None) -> dict[str, Any] | None:
            if value is None:
                return None
            return asdict(value)

        def score(value: ScoreAnswer | None) -> dict[str, Any] | None:
            if value is None:
                return None
            return asdict(value)

        return {
            "regime": choice(self.regime),
            "direction": choice(self.direction),
            "toxic_flow": self.toxic_flow,
            "liquidity_stressed": self.liquidity_stressed,
            "quote_environment": score(self.quote_environment),
            "inventory_pressure": score(self.inventory_pressure),
            "model_version": self.model_version,
            "latency_ms": self.latency_ms,
            "stale": self.stale,
            "error": self.error,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class Quote:
    price: float
    qty: float


@dataclass(frozen=True)
class ActionDecision:
    action: Action
    reason: str
    source: str
    skew_bps: float = 0.0
    spread_multiplier: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "source": self.source,
            "skew_bps": self.skew_bps,
            "spread_multiplier": self.spread_multiplier,
        }
