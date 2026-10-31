"""Resting quotes fill only when a trade prints through them.

Binance spot charges Regular User maker/taker on fills only. Place and cancel
are free and there is no gas. Gas flags stay in FeeConfig for on-chain venues.
Nothing here talks to an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from jev_paper.config import FeeConfig
from jev_paper.types import Quote, TradeEvent


@dataclass
class PaperAccount:
    cash: float = 0.0
    inventory: float = 0.0
    avg_entry: float = 0.0
    fees_paid: float = 0.0
    gas_paid: float = 0.0
    realized_pnl: float = 0.0
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    day: str = ""
    bid: Quote | None = None
    ask: Quote | None = None
    halted: bool = False
    consecutive_api_errors: int = 0
    last_alert: str | None = None
    pending_events: list[dict[str, float | str]] = field(default_factory=list)

    def equity(self, mid: float) -> float:
        return self.cash + self.inventory * mid

    def unrealized(self, mid: float) -> float:
        if self.inventory == 0:
            return 0.0
        return self.inventory * (mid - self.avg_entry)

    def drawdown(self, mid: float) -> float:
        return max(0.0, self.peak_equity - self.equity(mid))

    def daily_pnl(self, mid: float) -> float:
        return self.equity(mid) - self.day_start_equity


def mark(account: PaperAccount, mid: float, ts_ms: int) -> float:
    day = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date().isoformat()
    equity = account.equity(mid)
    if account.day != day:
        account.day = day
        account.day_start_equity = equity
    if equity > account.peak_equity:
        account.peak_equity = equity
    return equity


def sync_quotes(
    account: PaperAccount,
    bid: Quote | None,
    ask: Quote | None,
    fees: FeeConfig,
    *,
    mid: float,
    requote_bps: float,
) -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    account.bid = _sync_side(account, "bid", account.bid, bid, fees, mid, requote_bps, events)
    account.ask = _sync_side(account, "ask", account.ask, ask, fees, mid, requote_bps, events)
    return events


def match_trade(account: PaperAccount, trade: TradeEvent, fees: FeeConfig) -> list[dict[str, float | str]]:
    bid_hit = trade.aggressor == "sell" and account.bid is not None and trade.price <= account.bid.price
    ask_hit = trade.aggressor == "buy" and account.ask is not None and trade.price >= account.ask.price
    if bid_hit and ask_hit:
        return []
    events: list[dict[str, float | str]] = []
    if bid_hit and account.bid is not None:
        qty = min(trade.qty, account.bid.qty)
        events.append(_fill(account, "buy", account.bid.price, qty, fees.maker_bps, fees, kind="maker_buy"))
        account.bid = _reduce(account.bid, qty)
    elif ask_hit and account.ask is not None:
        qty = min(trade.qty, account.ask.qty)
        events.append(_fill(account, "sell", account.ask.price, qty, fees.maker_bps, fees, kind="maker_sell"))
        account.ask = _reduce(account.ask, qty)
    return events


def flatten(
    account: PaperAccount,
    best_bid: float | None,
    best_ask: float | None,
    fees: FeeConfig,
) -> list[dict[str, float | str]]:
    mid = best_bid or best_ask or 0.0
    events = sync_quotes(account, None, None, fees, mid=mid, requote_bps=0.0)
    if account.inventory > 0 and best_bid is not None:
        events.append(_fill(account, "sell", best_bid, account.inventory, fees.taker_bps, fees, kind="taker_sell"))
    elif account.inventory < 0 and best_ask is not None:
        events.append(_fill(account, "buy", best_ask, abs(account.inventory), fees.taker_bps, fees, kind="taker_buy"))
    account.halted = True
    return events


def _sync_side(
    account: PaperAccount,
    side: str,
    old: Quote | None,
    new: Quote | None,
    fees: FeeConfig,
    mid: float,
    requote_bps: float,
    events: list[dict[str, float | str]],
) -> Quote | None:
    if _same(old, new, mid, requote_bps):
        return old
    if old is not None:
        events.append(_lifecycle(account, f"cancel_{side}", fees, fees.charge_cancel))
    if new is None:
        return None
    events.append(_lifecycle(account, f"place_{side}", fees, fees.charge_place))
    return new


def _same(old: Quote | None, new: Quote | None, mid: float, requote_bps: float) -> bool:
    if old is None or new is None:
        return old is None and new is None
    tolerance = abs(mid) * requote_bps / 10_000.0
    return abs(old.price - new.price) <= tolerance and abs(old.qty - new.qty) <= 1e-12


def _lifecycle(account: PaperAccount, kind: str, fees: FeeConfig, charge: bool) -> dict[str, float | str]:
    gas = fees.gas_per_event if charge else 0.0
    if gas:
        account.cash -= gas
        account.gas_paid += gas
    return {"kind": kind, "gas": gas, "fee": 0.0, "qty": 0.0, "price": 0.0}


def _fill(
    account: PaperAccount,
    side: str,
    price: float,
    qty: float,
    fee_bps: float,
    fees: FeeConfig,
    *,
    kind: str,
) -> dict[str, float | str]:
    if fees.bnb_discount:
        fee_bps *= 0.75
    fee = price * qty * fee_bps / 10_000.0
    account.fees_paid += fee
    account.cash -= fee
    gas = fees.gas_per_event if fees.charge_fill_gas else 0.0
    if gas:
        account.cash -= gas
        account.gas_paid += gas
    if side == "buy":
        account.cash -= price * qty
        _apply_inventory(account, qty, price)
    else:
        account.cash += price * qty
        _apply_inventory(account, -qty, price)
    return {"kind": kind, "gas": gas, "fee": fee, "qty": qty, "price": price}


def _apply_inventory(account: PaperAccount, delta: float, price: float) -> None:
    inventory = account.inventory
    if delta == 0:
        return
    if inventory == 0 or inventory * delta > 0:
        new_abs = abs(inventory) + abs(delta)
        account.avg_entry = (abs(inventory) * account.avg_entry + abs(delta) * price) / new_abs
        account.inventory = inventory + delta
        return
    closed = min(abs(inventory), abs(delta))
    if inventory > 0:
        account.realized_pnl += (price - account.avg_entry) * closed
    else:
        account.realized_pnl += (account.avg_entry - price) * closed
    remainder = abs(delta) - closed
    if remainder == 0:
        account.inventory = inventory + delta
        if account.inventory == 0:
            account.avg_entry = 0.0
        return
    account.inventory = remainder if delta > 0 else -remainder
    account.avg_entry = price


def _reduce(quote: Quote, qty: float) -> Quote | None:
    left = quote.qty - qty
    if left <= 1e-12:
        return None
    return Quote(quote.price, left)
