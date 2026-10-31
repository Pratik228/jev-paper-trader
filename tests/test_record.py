from __future__ import annotations

import gzip
import json
from pathlib import Path

from jev_paper.clock import WallClock
from jev_paper.config import load_config
from jev_paper.data.feed import read_events
from jev_paper.data.record import DayWriter, Recorder
from jev_paper.loop import run_replay
from jev_paper.types import BookEvent, BookLevel, TradeEvent


class FakeClock(WallClock):
    def __init__(self, t: int) -> None:
        self.t = t

    def now_ms(self) -> int:
        return self.t


def _book(ts: int, mid: float = 100.0) -> BookEvent:
    return BookEvent(
        ts_ms=ts,
        venue="binance",
        pair="BTCUSDT",
        bids=(BookLevel(mid - 0.05, 1.0),),
        asks=(BookLevel(mid + 0.05, 1.0),),
        recv_ts_ms=ts,
    )


def test_day_writer_gzip_and_replay(tmp_path: Path) -> None:
    writer = DayWriter(tmp_path / "raw")
    writer.write(_book(1_700_000_000_000))
    writer.write(TradeEvent(1_700_000_000_200, "binance", "BTCUSDT", 100.0, 0.1, "buy", recv_ts_ms=1_700_000_000_200))
    live = next((tmp_path / "raw").glob("*.jsonl"))
    assert live.stat().st_size > 0
    writer.close()
    files = list((tmp_path / "raw").glob("*.jsonl.gz"))
    assert files
    with gzip.open(files[0], "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    assert rows[0]["kind"] == "book"
    events = list(read_events(tmp_path / "raw"))
    assert len(events) == 2
    run_replay(events, load_config(Path(__file__).resolve().parents[1] / "config.yaml"), tmp_path / "logs")
    assert (tmp_path / "logs" / "outcomes_jev_gated.jsonl").exists()


def test_gap_and_heartbeat(tmp_path: Path) -> None:
    clock = FakeClock(1_000)
    recorder = Recorder(DayWriter(tmp_path / "raw"), tmp_path / "gaps.jsonl", clock=clock, gap_ms=500, heartbeat_ms=1_000)
    recorder.handle(_book(1_000))
    clock.t = 2_000
    recorder.handle(_book(2_000))
    recorder.disconnect("ConnectionError")
    clock.t = 4_000
    recorder.heartbeat_once()
    recorder.stop()
    gaps = [json.loads(line) for line in (tmp_path / "gaps.jsonl").read_text().splitlines() if line]
    kinds = {row["kind"] for row in gaps}
    assert "timestamp_jump" in kinds
    assert "disconnect" in kinds
    heart = (tmp_path / "heartbeat.jsonl").read_text()
    assert "heartbeat" in heart
