"""Numeric snapshot from events at or before the decision time."""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from typing import TypeVar

T = TypeVar("T")

from jev_paper.config import StateConfig
from jev_paper.types import AccountView, BookEvent, Event, Snapshot, TradeEvent


class StateEngine:
    def __init__(self, config: StateConfig) -> None:
        self.config = config
        self._books: list[BookEvent] = []
        self._book_ts: list[int] = []
        self._trades: list[TradeEvent] = []
        self._trade_ts: list[int] = []
        self._mids: list[tuple[int, float]] = []
        self._mid_ts: list[int] = []
        self._latencies: list[tuple[int, float]] = []

    def update(self, event: Event) -> None:
        if isinstance(event, BookEvent):
            self._books.append(event)
            self._book_ts.append(event.ts_ms)
            mid = _mid(event)
            if mid is not None:
                self._mids.append((event.ts_ms, mid))
                self._mid_ts.append(event.ts_ms)
            if event.recv_ts_ms is not None:
                self._latencies.append((event.ts_ms, max(0.0, float(event.recv_ts_ms - event.ts_ms))))
        else:
            self._trades.append(event)
            self._trade_ts.append(event.ts_ms)
        self._prune(event.ts_ms)

    def _prune(self, ts_ms: int) -> None:
        keep = ts_ms - self.config.vol_24h_ms
        self._books, self._book_ts = _drop_before(self._books, self._book_ts, keep)
        self._trades, self._trade_ts = _drop_before(self._trades, self._trade_ts, keep)
        self._mids, self._mid_ts = _drop_before(self._mids, self._mid_ts, keep)
        if self._latencies:
            kept = [item for item in self._latencies if item[0] >= keep]
            if kept and kept[0][0] > keep and self._latencies[0][0] < kept[0][0]:
                older = [item for item in self._latencies if item[0] < kept[0][0]]
                kept = older[-1:] + kept
            self._latencies = kept[-10_000:]

    def snapshot(self, ts_ms: int, block_id: int, account: AccountView) -> Snapshot:
        book = self._book_asof(ts_ms)
        mid = self._mid_asof(ts_ms)
        micro = _microprice(book) if book else None
        spread = _spread_bps(book, mid) if book else None
        depth_bid, depth_ask = _depth(book)
        imbalance = _imbalance(book)
        ret_1m = self._return(ts_ms, self.config.ret_1m_ms)
        ret_5m = self._return(ts_ms, self.config.ret_5m_ms)
        ret_30m = self._return(ts_ms, self.config.ret_30m_ms)
        vol_5 = self._vol(ts_ms, self.config.ret_5m_ms)
        vol_30 = self._vol(ts_ms, self.config.ret_30m_ms)
        vol_24 = self._vol(ts_ms, self.config.vol_24h_ms)
        vol_ratio = vol_5 / vol_24 if vol_5 is not None and vol_24 not in (None, 0.0) else None
        ratio = self._aggressive_buy_ratio(ts_ms)
        latency = [item[1] for item in self._latencies if item[0] <= ts_ms][-10:]
        warmup = mid is None or ret_1m is None
        return Snapshot(
            block_id=block_id,
            ts_ms=ts_ms,
            mid=mid,
            microprice=micro,
            spread_bps=spread,
            depth_bid=depth_bid,
            depth_ask=depth_ask,
            imbalance=imbalance,
            ret_1m=ret_1m,
            ret_5m=ret_5m,
            ret_30m=ret_30m,
            aggressive_buy_ratio=ratio,
            realized_vol_5m=vol_5,
            realized_vol_30m=vol_30,
            vol_vs_24h=vol_ratio,
            inventory=account.inventory,
            unrealized_pnl=account.unrealized_pnl,
            drawdown=account.drawdown,
            latency_ms_last10=latency,
            warmup=warmup,
        )

    def _book_asof(self, ts_ms: int) -> BookEvent | None:
        index = bisect_right(self._book_ts, ts_ms) - 1
        if index < 0:
            return None
        return self._books[index]

    def _mid_asof(self, ts_ms: int) -> float | None:
        index = bisect_right(self._mid_ts, ts_ms) - 1
        if index < 0:
            return None
        return self._mids[index][1]

    def _return(self, ts_ms: int, window_ms: int) -> float | None:
        target = ts_ms - window_ms
        if not self._mid_ts or self._mid_ts[0] > target:
            return None
        now = self._mid_asof(ts_ms)
        then = self._mid_asof(target)
        if now is None or then is None or then == 0:
            return None
        return now / then - 1.0

    def _vol(self, ts_ms: int, window_ms: int) -> float | None:
        start = ts_ms - window_ms
        if not self._mid_ts or self._mid_ts[0] > start:
            return None
        hi = bisect_right(self._mid_ts, ts_ms)
        lo = bisect_right(self._mid_ts, start) - 1
        lo = max(lo, 0)
        sample = self.config.vol_sample_ms
        points: list[float] = []
        next_t = self._mid_ts[lo]
        for ts_i, mid in self._mids[lo:hi]:
            if ts_i >= next_t:
                points.append(mid)
                next_t = ts_i + sample
        if len(points) < 3:
            return None
        rets = [math.log(points[i] / points[i - 1]) for i in range(1, len(points)) if points[i - 1] > 0 and points[i] > 0]
        if len(rets) < 2:
            return None
        return statistics.stdev(rets)

    def _aggressive_buy_ratio(self, ts_ms: int) -> float | None:
        start = ts_ms - self.config.trade_window_ms
        hi = bisect_right(self._trade_ts, ts_ms)
        lo = bisect_right(self._trade_ts, start - 1)
        buy = 0.0
        total = 0.0
        for trade in self._trades[lo:hi]:
            if trade.ts_ms < start:
                continue
            total += trade.qty
            if trade.aggressor == "buy":
                buy += trade.qty
        if total <= 0:
            return None
        return buy / total


def _drop_before(rows: list[T], stamps: list[int], keep: int) -> tuple[list[T], list[int]]:
    if not stamps or stamps[0] >= keep:
        return rows, stamps
    index = bisect_right(stamps, keep) - 1
    index = max(index, 0)
    if index == 0:
        return rows, stamps
    return rows[index:], stamps[index:]


def _mid(book: BookEvent) -> float | None:
    if not book.bids or not book.asks:
        return None
    return (book.bids[0].price + book.asks[0].price) / 2.0


def _microprice(book: BookEvent) -> float | None:
    if not book.bids or not book.asks:
        return None
    bid, ask = book.bids[0], book.asks[0]
    denom = bid.qty + ask.qty
    if denom <= 0:
        return None
    return (ask.price * bid.qty + bid.price * ask.qty) / denom


def _spread_bps(book: BookEvent, mid: float | None) -> float | None:
    if not book.bids or not book.asks or not mid:
        return None
    return (book.asks[0].price - book.bids[0].price) / mid * 10_000.0


def _depth(book: BookEvent | None) -> tuple[list[float], list[float]]:
    if book is None:
        return [], []
    bids = [level.qty for level in book.bids[:3]]
    asks = [level.qty for level in book.asks[:3]]
    return bids, asks


def _imbalance(book: BookEvent | None) -> float | None:
    if book is None or not book.bids or not book.asks:
        return None
    bid_notional = sum(level.price * level.qty for level in book.bids[:3])
    ask_notional = sum(level.price * level.qty for level in book.asks[:3])
    denom = bid_notional + ask_notional
    if denom <= 0:
        return None
    return (bid_notional - ask_notional) / denom
