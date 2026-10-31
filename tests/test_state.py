from __future__ import annotations

import json

from jev_paper.config import StateConfig
from jev_paper.state.engine import StateEngine
from jev_paper.types import AccountView, BookEvent, BookLevel, TradeEvent

ZERO = AccountView(0.0, 0.0, 0.0)


def _book(ts: int, mid: float) -> BookEvent:
    return BookEvent(
        ts_ms=ts,
        venue="binance",
        pair="BTCUSDT",
        bids=(BookLevel(mid - 0.05, 2.0), BookLevel(mid - 0.15, 3.0), BookLevel(mid - 0.25, 4.0)),
        asks=(BookLevel(mid + 0.05, 1.0), BookLevel(mid + 0.15, 1.5), BookLevel(mid + 0.25, 2.0)),
        recv_ts_ms=ts + 8,
    )


def test_snapshot_fields_and_token_budget() -> None:
    engine = StateEngine(StateConfig(ret_1m_ms=1000, ret_5m_ms=2000, ret_30m_ms=3000, vol_24h_ms=10_000, trade_window_ms=1000, vol_sample_ms=500))
    for ts, mid in ((0, 100.0), (500, 100.2), (1000, 101.0), (1500, 101.5), (2000, 102.0)):
        engine.update(_book(ts, mid))
    engine.update(TradeEvent(1600, "binance", "BTCUSDT", 101.0, 1.0, "buy", recv_ts_ms=1602))
    engine.update(TradeEvent(1700, "binance", "BTCUSDT", 101.0, 1.0, "sell", recv_ts_ms=1702))
    snap = engine.snapshot(2000, 3, AccountView(0.25, 1.5, 0.4))
    assert snap.mid == 102.0
    assert snap.microprice is not None
    assert snap.spread_bps is not None and snap.spread_bps > 0
    assert len(snap.depth_bid) == 3 and len(snap.depth_ask) == 3
    assert snap.ret_1m is not None
    assert snap.aggressive_buy_ratio == 0.5
    assert snap.inventory == 0.25
    assert snap.latency_ms_last10[-1] == 8
    assert len(json.dumps(snap.compact())) / 4 < 400


def test_no_lookahead() -> None:
    engine = StateEngine(StateConfig())
    engine.update(_book(1_000, 100.0))
    engine.update(_book(2_000, 100.5))
    before = engine.snapshot(2_000, 1, ZERO)
    engine.update(_book(9_000, 250.0))
    engine.update(TradeEvent(8_000, "binance", "BTCUSDT", 240.0, 5.0, "buy"))
    after = engine.snapshot(2_000, 1, ZERO)
    later = engine.snapshot(9_000, 2, ZERO)
    assert before.mid == after.mid == 100.5
    assert before.ret_1m == after.ret_1m
    assert before.aggressive_buy_ratio == after.aggressive_buy_ratio
    assert later.mid == 250.0
    assert later.mid != after.mid


def test_missing_history_is_null_not_zero() -> None:
    engine = StateEngine(StateConfig())
    engine.update(_book(1_000, 100.0))
    snap = engine.snapshot(1_000, 0, ZERO)
    assert snap.ret_1m is None
    assert snap.realized_vol_5m is None
    assert snap.vol_vs_24h is None
    assert snap.warmup is True
