"""Summarize recorded market data: counts, gaps, hours, disk size."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from jev_paper.jsonlog import read_jsonl


def summarize(data_dir: Path) -> dict[str, Any]:
    raw = data_dir / "raw"
    files = sorted(list(raw.glob("*.jsonl")) + list(raw.glob("*.jsonl.gz"))) if raw.exists() else []
    days: list[dict[str, Any]] = []
    total_events = 0
    total_bytes = 0
    for path in files:
        events, first_ts, last_ts, books, trades = _scan(path)
        hours = 0.0 if first_ts is None or last_ts is None else max(0.0, (last_ts - first_ts) / 3_600_000.0)
        size = path.stat().st_size
        total_events += events
        total_bytes += size
        days.append(
            {
                "day": path.name.replace(".jsonl.gz", "").replace(".jsonl", ""),
                "file": path.name,
                "events": events,
                "books": books,
                "trades": trades,
                "hours": round(hours, 3),
                "bytes": size,
            }
        )
    gaps = read_jsonl(data_dir / "gaps.jsonl")
    return {
        "data_dir": str(data_dir),
        "days": days,
        "event_total": total_events,
        "gap_count": len(gaps),
        "bytes": total_bytes,
        "hours_covered": round(sum(float(day["hours"]) for day in days), 3),
    }


def _scan(path: Path) -> tuple[int, int | None, int | None, int, int]:
    opener: Any = gzip.open if path.name.endswith(".gz") else open
    events = 0
    books = 0
    trades = 0
    first_ts: int | None = None
    last_ts: int | None = None
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            events += 1
            kind = row.get("kind")
            if kind == "book":
                books += 1
            elif kind == "trade":
                trades += 1
            ts = int(row.get("ts_ms") or 0)
            if first_ts is None:
                first_ts = ts
            last_ts = ts
    return events, first_ts, last_ts, books, trades


def render(summary: dict[str, Any]) -> str:
    lines = [
        f"data_dir {summary['data_dir']}",
        f"days {len(summary['days'])}  events {summary['event_total']}  "
        f"gaps {summary['gap_count']}  hours {summary['hours_covered']}  "
        f"disk {summary['bytes']} bytes",
    ]
    for day in summary["days"]:
        lines.append(
            f"  {day['day']}: events={day['events']} books={day['books']} "
            f"trades={day['trades']} hours={day['hours']} bytes={day['bytes']}"
        )
    if not summary["days"]:
        lines.append("  no files under data/raw/. Run make record.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check recorded Binance tapes")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    text = render(summarize(Path(args.data_dir)))
    print(text, end="")


if __name__ == "__main__":
    main()
