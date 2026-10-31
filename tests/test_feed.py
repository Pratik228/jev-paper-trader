from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_paper.config import FeedConfig
from jev_paper.data.feed import LiveFeed, event_from_dict, parse_message, read_events

FIXTURE = Path(__file__).parent / "fixtures" / "sample_events.jsonl"


def test_parse_binance_trade_and_depth() -> None:
    trade = parse_message(
        {"stream": "btcusdt@trade", "data": {"e": "trade", "s": "BTCUSDT", "p": "100.5", "q": "0.2", "T": 50, "m": True}},
        venue="binance",
        pair="BTCUSDT",
        recv_ts_ms=55,
    )
    assert trade[0].kind == "trade"
    assert trade[0].aggressor == "sell"
    assert trade[0].price == 100.5
    book = parse_message(
        {"bids": [["100", "1"], ["99", "2"]], "asks": [["101", "1.5"]], "E": 40},
        venue="binance",
        pair="BTCUSDT",
        recv_ts_ms=45,
    )
    assert book[0].kind == "book"
    assert book[0].bids[0].price == 100
    assert book[0].ts_ms == 40


def test_replay_fixture_roundtrip() -> None:
    events = list(read_events(FIXTURE))
    assert any(event.kind == "book" for event in events)
    assert any(event.kind == "trade" for event in events)
    restored = event_from_dict(json.loads(json.dumps({
        "kind": "trade",
        "ts_ms": 1,
        "venue": "binance",
        "pair": "BTCUSDT",
        "price": 1,
        "qty": 1,
        "aggressor": "buy",
    })))
    assert restored.kind == "trade"


def test_poll_fallback_after_websocket_failures() -> None:
    class DeadSocket:
        def settimeout(self, timeout: float) -> None:
            return None

        def connect(self, url: str) -> None:
            return None

        def recv(self) -> str:
            raise ConnectionError("down")

        def close(self) -> None:
            return None

    calls: list[str] = []

    class Body:
        def __init__(self, payload: str) -> None:
            self.payload = payload

        def read(self) -> bytes:
            return self.payload.encode()

        def close(self) -> None:
            return None

    def opener(url: str, timeout: float = 0) -> Body:
        calls.append(url)
        if "depth" in url:
            return Body('{"bids":[["100","1"]],"asks":[["101","1"]],"E":10}')
        return Body('[{"price":"100.2","qty":"0.1","time":11,"isBuyerMaker":false}]')

    config = FeedConfig(ws_fails_before_poll=3, reconnect_max_s=0)
    feed = LiveFeed(config, socket_factory=DeadSocket, opener=opener, sleep=lambda _seconds: None)
    original = feed._poll

    def stop_after_poll(handler: object) -> None:
        original(handler)  # type: ignore[arg-type]
        feed.stop()

    feed._poll = stop_after_poll  # type: ignore[method-assign]
    seen: list[object] = []
    feed.run(seen.append)
    assert any("depth" in url for url in calls)
    assert any(getattr(event, "kind", "") == "book" for event in seen)
    assert any(getattr(event, "kind", "") == "trade" for event in seen)


def test_rest_trade_aggressor() -> None:
    events = parse_message(
        {"price": "10", "qty": "1", "time": 3, "isBuyerMaker": False},
        venue="binance",
        pair="BTCUSDT",
        recv_ts_ms=4,
    )
    assert events[0].aggressor == "buy"
