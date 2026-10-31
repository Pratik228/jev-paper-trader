from __future__ import annotations

from jev_paper.config import FeeConfig
from jev_paper.sim.fills import PaperAccount, flatten, match_trade, sync_quotes
from jev_paper.types import Quote, TradeEvent

BINANCE = FeeConfig(
    venue="binance",
    maker_bps=10.0,
    taker_bps=10.0,
    gas_per_event=0.0,
    charge_place=False,
    charge_cancel=False,
    charge_fill_gas=False,
)
ONCHAIN = FeeConfig(
    venue="onchain",
    maker_bps=2.0,
    taker_bps=4.0,
    gas_per_event=0.01,
    charge_place=True,
    charge_cancel=True,
    charge_fill_gas=True,
)


def test_binance_charges_fees_only_on_fills() -> None:
    account = PaperAccount()
    sync_quotes(account, Quote(100.0, 1.0), Quote(102.0, 1.0), BINANCE, mid=101.0, requote_bps=1.0)
    assert account.gas_paid == 0.0
    assert account.cash == 0.0
    missed = match_trade(account, TradeEvent(1, "binance", "BTCUSDT", 100.5, 0.4, "sell"), BINANCE)
    assert missed == []
    match_trade(account, TradeEvent(2, "binance", "BTCUSDT", 100.0, 0.5, "sell"), BINANCE)
    assert account.inventory == 0.5
    assert abs(account.fees_paid - 0.05) < 1e-9
    assert account.gas_paid == 0.0
    sync_quotes(account, None, None, BINANCE, mid=101.0, requote_bps=0.0)
    assert account.gas_paid == 0.0
    assert account.fees_paid == 0.05


def test_onchain_gas_on_place_cancel_and_fill() -> None:
    account = PaperAccount()
    sync_quotes(account, Quote(100.0, 1.0), Quote(102.0, 1.0), ONCHAIN, mid=101.0, requote_bps=1.0)
    assert account.gas_paid == 0.02
    match_trade(account, TradeEvent(2, "binance", "BTCUSDT", 100.0, 0.5, "sell"), ONCHAIN)
    assert abs(account.fees_paid - 0.01) < 1e-9
    assert abs(account.gas_paid - 0.03) < 1e-9
    flatten(account, best_bid=110.0, best_ask=111.0, fees=ONCHAIN)
    assert account.halted
    assert account.inventory == 0.0
    assert account.gas_paid > 0.03


def test_unchanged_quote_is_not_recanceled() -> None:
    account = PaperAccount()
    sync_quotes(account, Quote(100.0, 1.0), Quote(101.0, 1.0), ONCHAIN, mid=100.5, requote_bps=5.0)
    paid = account.gas_paid
    sync_quotes(account, Quote(100.01, 1.0), Quote(101.01, 1.0), ONCHAIN, mid=100.5, requote_bps=5.0)
    assert account.gas_paid == paid
