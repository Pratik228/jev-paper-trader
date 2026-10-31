"""Binance public book and trades, with replay, reconnect, and REST fallback.

This module only reads public market data. It does not sign or place orders.
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from jev_paper.clock import WallClock
from jev_paper.config import FeedConfig
from jev_paper.types import BookEvent, BookLevel, Event, TradeEvent


class SocketLike(Protocol):
    def settimeout(self, timeout: float) -> None: ...
    def connect(self, url: str) -> None: ...
    def recv(self) -> str | bytes: ...
    def close(self) -> None: ...


def _levels(raw: list[Any], *, reverse: bool) -> tuple[BookLevel, ...]:
    levels: list[BookLevel] = []
    for row in raw:
        if isinstance(row, dict):
            price, qty = float(row["price"]), float(row["qty"])
        else:
            price, qty = float(row[0]), float(row[1])
        if qty > 0 and price > 0:
            levels.append(BookLevel(price, qty))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return tuple(levels[:5])


def parse_book(
    payload: dict[str, Any],
    *,
    venue: str,
    pair: str,
    recv_ts_ms: int,
) -> BookEvent:
    ts = int(payload.get("E") or payload.get("T") or recv_ts_ms)
    return BookEvent(
        ts_ms=ts,
        venue=venue,
        pair=pair,
        bids=_levels(list(payload.get("bids") or payload.get("b") or []), reverse=True),
        asks=_levels(list(payload.get("asks") or payload.get("a") or []), reverse=False),
        recv_ts_ms=recv_ts_ms,
    )


def parse_trade(
    payload: dict[str, Any],
    *,
    venue: str,
    pair: str,
    recv_ts_ms: int,
) -> TradeEvent:
    ts = int(payload.get("T") or payload.get("E") or payload.get("time") or recv_ts_ms)
    price = float(payload.get("p") or payload["price"])
    qty = float(payload.get("q") or payload["qty"])
    if "m" in payload:
        aggressor: Any = "sell" if payload["m"] else "buy"
    else:
        aggressor = "sell" if payload.get("isBuyerMaker") else "buy"
    return TradeEvent(
        ts_ms=ts,
        venue=venue,
        pair=str(payload.get("s") or pair),
        price=price,
        qty=qty,
        aggressor=aggressor,
        recv_ts_ms=recv_ts_ms,
    )


def parse_message(
    payload: dict[str, Any],
    *,
    venue: str,
    pair: str,
    recv_ts_ms: int,
) -> list[Event]:
    if "data" in payload and "stream" in payload:
        payload = payload["data"]
    if not isinstance(payload, dict):
        return []
    if payload.get("e") == "trade" or "isBuyerMaker" in payload or (
        "p" in payload and "q" in payload and "m" in payload
    ):
        return [parse_trade(payload, venue=venue, pair=pair, recv_ts_ms=recv_ts_ms)]
    if "bids" in payload or "asks" in payload or "b" in payload or "a" in payload:
        return [parse_book(payload, venue=venue, pair=pair, recv_ts_ms=recv_ts_ms)]
    return []


def event_to_dict(event: Event) -> dict[str, Any]:
    if isinstance(event, BookEvent):
        return {
            "kind": "book",
            "ts_ms": event.ts_ms,
            "recv_ts_ms": event.recv_ts_ms,
            "venue": event.venue,
            "pair": event.pair,
            "bids": [{"price": level.price, "qty": level.qty} for level in event.bids],
            "asks": [{"price": level.price, "qty": level.qty} for level in event.asks],
        }
    return {
        "kind": "trade",
        "ts_ms": event.ts_ms,
        "recv_ts_ms": event.recv_ts_ms,
        "venue": event.venue,
        "pair": event.pair,
        "price": event.price,
        "qty": event.qty,
        "aggressor": event.aggressor,
    }


def event_from_dict(raw: dict[str, Any]) -> Event:
    kind = raw.get("kind")
    if kind == "trade":
        return TradeEvent(
            ts_ms=int(raw["ts_ms"]),
            venue=str(raw["venue"]),
            pair=str(raw["pair"]),
            price=float(raw["price"]),
            qty=float(raw["qty"]),
            aggressor=raw["aggressor"],
            recv_ts_ms=raw.get("recv_ts_ms"),
        )
    return BookEvent(
        ts_ms=int(raw["ts_ms"]),
        venue=str(raw["venue"]),
        pair=str(raw["pair"]),
        bids=_levels(list(raw.get("bids") or []), reverse=True),
        asks=_levels(list(raw.get("asks") or []), reverse=False),
        recv_ts_ms=raw.get("recv_ts_ms"),
    )


def read_events(path: str | Path) -> Iterator[Event]:
    for file_path in _event_files(path):
        opener: Any = gzip.open if _gzipped(file_path) else open
        with opener(file_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield event_from_dict(json.loads(line))


def _gzipped(path: Path) -> bool:
    return path.name.endswith(".gz")


def _event_files(path: str | Path) -> list[Path]:
    target = Path(path)
    if target.is_dir():
        files = list(target.glob("*.jsonl")) + list(target.glob("*.jsonl.gz"))
        return sorted({item.resolve() for item in files}, key=lambda item: item.name)
    if not target.exists():
        raise FileNotFoundError(target)
    return [target]


def _default_socket() -> SocketLike:
    import websocket

    return websocket.WebSocket()


class LiveFeed:
    """Public websocket with reconnect. After repeated failures, poll public REST."""

    def __init__(
        self,
        config: FeedConfig,
        *,
        socket_factory: Callable[[], SocketLike] | None = None,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: WallClock | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._socket_factory = socket_factory or _default_socket
        self._opener = opener or urlopen
        self._sleep = sleep or time.sleep
        self._clock = clock or WallClock()
        self._on_disconnect = on_disconnect
        self._stop = False
        self._socket: SocketLike | None = None

    def stop(self) -> None:
        self._stop = True
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass

    def run(self, handler: Callable[[Event], None]) -> None:
        backoff = 1.0
        fails = 0
        while not self._stop:
            try:
                self._consume_ws(handler)
                fails = 0
                backoff = 1.0
            except Exception as exc:
                if self._stop:
                    return
                if self._on_disconnect:
                    self._on_disconnect(exc.__class__.__name__)
                fails += 1
                if fails >= self.config.ws_fails_before_poll:
                    try:
                        self._poll(handler)
                    except Exception:
                        pass
                    fails = 0
                self._sleep(backoff)
                backoff = min(backoff * 2, self.config.reconnect_max_s)

    def _consume_ws(self, handler: Callable[[Event], None]) -> None:
        socket = self._socket_factory()
        self._socket = socket
        socket.settimeout(self.config.request_timeout_s)
        socket.connect(self.config.websocket_url)
        while not self._stop:
            raw = socket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            recv = self._clock.now_ms()
            for event in parse_message(
                payload,
                venue=self.config.venue,
                pair=self.config.pair,
                recv_ts_ms=recv,
            ):
                handler(event)

    def _poll(self, handler: Callable[[Event], None]) -> None:
        symbol = self.config.pair
        depth_url = self.config.rest_depth_url + "?" + urlencode({"symbol": symbol, "limit": 20})
        trades_url = self.config.rest_trades_url + "?" + urlencode({"symbol": symbol, "limit": 50})
        recv = self._clock.now_ms()
        depth = json.loads(self._read(depth_url))
        for event in parse_message(depth, venue=self.config.venue, pair=symbol, recv_ts_ms=recv):
            handler(event)
        trades = json.loads(self._read(trades_url))
        if isinstance(trades, dict):
            trades = trades.get("data") or []
        for row in trades:
            for event in parse_message(row, venue=self.config.venue, pair=symbol, recv_ts_ms=recv):
                handler(event)

    def _read(self, url: str) -> str:
        response = self._opener(url, timeout=self.config.request_timeout_s)
        try:
            body = response.read()
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if isinstance(body, bytes):
            return body.decode("utf-8")
        return str(body)
