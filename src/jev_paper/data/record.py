"""Record-only mode. Public Binance book and trades, no strategy, no orders."""

from __future__ import annotations

import argparse
import gzip
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from jev_paper.clock import WallClock
from jev_paper.config import load_config
from jev_paper.data.feed import LiveFeed, event_to_dict
from jev_paper.jsonlog import append_jsonl
from jev_paper.types import Event

HEARTBEAT_MS = 60_000
GAP_MS = 2_000


class DayWriter:
    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._day = ""
        self._handle: gzip.GzipFile | TextIO | None = None
        self._lock = threading.Lock()

    def write(self, event: Event) -> None:
        payload = json.dumps(event_to_dict(event), separators=(",", ":")) + "\n"
        day = datetime.fromtimestamp(event.ts_ms / 1000.0, tz=timezone.utc).date().isoformat()
        with self._lock:
            self._rotate(day)
            assert self._handle is not None
            self._handle.write(payload.encode("utf-8"))

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def _rotate(self, day: str) -> None:
        if day == self._day and self._handle is not None:
            return
        if self._handle is not None:
            self._handle.close()
        path = self.raw_dir / f"{day}.jsonl.gz"
        self._handle = gzip.open(path, "ab")
        self._day = day


class Recorder:
    def __init__(
        self,
        writer: DayWriter,
        gaps_path: Path,
        *,
        clock: WallClock | None = None,
        gap_ms: int = GAP_MS,
        heartbeat_ms: int = HEARTBEAT_MS,
    ) -> None:
        self.writer = writer
        self.gaps_path = gaps_path
        self.clock = clock or WallClock()
        self.gap_ms = gap_ms
        self.heartbeat_ms = heartbeat_ms
        self.last_event_ts: int | None = None
        self.last_recv_ts: int | None = None
        self.events = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def handle(self, event: Event) -> None:
        now = self.clock.now_ms()
        with self._lock:
            if self.last_event_ts is not None and event.ts_ms - self.last_event_ts > self.gap_ms:
                self._gap("timestamp_jump", self.last_event_ts, event.ts_ms, now)
            if self.last_recv_ts is not None and now - self.last_recv_ts > self.gap_ms:
                self._gap("recv_silence", self.last_recv_ts, now, now)
            self.last_event_ts = event.ts_ms
            self.last_recv_ts = now
            self.events += 1
        self.writer.write(event)

    def disconnect(self, reason: str) -> None:
        now = self.clock.now_ms()
        with self._lock:
            start = self.last_recv_ts or now
            self._gap("disconnect", start, now, now, reason=reason)

    def heartbeat_once(self) -> dict[str, Any]:
        now = self.clock.now_ms()
        with self._lock:
            age = None if self.last_recv_ts is None else now - self.last_recv_ts
            record = {
                "kind": "heartbeat",
                "ts_ms": now,
                "events": self.events,
                "last_event_ts": self.last_event_ts,
                "age_ms": age,
            }
            if self.last_recv_ts is not None and now - self.last_recv_ts > self.gap_ms:
                self._gap("heartbeat_silence", self.last_recv_ts, now, now)
        append_jsonl(self.gaps_path.parent / "heartbeat.jsonl", record)
        return record

    def run_heartbeats(self) -> None:
        while not self._stop.wait(self.heartbeat_ms / 1000.0):
            self.heartbeat_once()

    def stop(self) -> None:
        self._stop.set()
        self.writer.close()

    def _gap(self, kind: str, start_ms: int, end_ms: int, now_ms: int, reason: str = "") -> None:
        append_jsonl(
            self.gaps_path,
            {
                "kind": kind,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "gap_ms": max(0, end_ms - start_ms),
                "logged_ts_ms": now_ms,
                "reason": reason,
            },
        )


def run_record(config_path: Path, data_dir: Path | None = None) -> None:
    config = load_config(config_path)
    root = data_dir or Path(config.data_dir)
    recorder = Recorder(DayWriter(root / "raw"), root / "gaps.jsonl")
    feed = LiveFeed(config.feed, on_disconnect=recorder.disconnect)
    thread = threading.Thread(target=recorder.run_heartbeats, name="heartbeat", daemon=True)
    thread.start()
    try:
        feed.run(recorder.handle)
    finally:
        feed.stop()
        recorder.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record public Binance book and trades. No orders.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()
    config = load_config(args.config)
    run_record(Path(args.config), Path(args.data_dir) if args.data_dir else Path(config.data_dir))


if __name__ == "__main__":
    main()
