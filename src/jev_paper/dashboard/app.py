"""Read-only dashboard. No route here can change a quote or a config value."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse

from jev_paper.jsonlog import iter_jsonl, read_tail_jsonl
from jev_paper.metrics import (
    STRATEGIES,
    calibration,
    latency_histogram,
    regime_breakdown,
    stale_rate,
    strategy_summary,
)

_STATIC = Path(__file__).resolve().parent / "static" / "index.html"


def create_app(log_dir: Path, report_dir: Path) -> FastAPI:
    app = FastAPI(title="Jev paper dashboard")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True, "trading_controls": False}

    @app.get("/api/live")
    def live() -> dict[str, Any]:
        latest = {name: _last(log_dir / f"outcomes_{name}.jsonl") for name in STRATEGIES}
        snap_rows = [
            row
            for row in read_tail_jsonl(log_dir / "snapshots.jsonl", max_bytes=250_000)
            if row.get("strategy") == "baseline"
        ]
        return {
            "snapshot": snap_rows[-1] if snap_rows else None,
            "strategies": latest,
            "alerts": read_tail_jsonl(log_dir / "alerts.jsonl", max_bytes=100_000)[-5:],
        }

    @app.get("/api/decisions")
    def decisions() -> dict[str, Any]:
        rows = read_tail_jsonl(log_dir / "decisions.jsonl")
        return {"decisions": rows[-100:]}

    @app.get("/api/pnl")
    def pnl() -> dict[str, Any]:
        return {"strategies": {name: _stream_pnl(log_dir / f"outcomes_{name}.jsonl") for name in STRATEGIES}}

    @app.get("/api/calibration")
    def calibration_route() -> dict[str, Any]:
        decisions = read_tail_jsonl(log_dir / "decisions.jsonl")
        snapshots = read_tail_jsonl(log_dir / "snapshots.jsonl")
        outcomes = read_tail_jsonl(log_dir / "outcomes.jsonl")
        return {
            "buckets": calibration(decisions, snapshots),
            "regimes": regime_breakdown(decisions, outcomes),
            "latency": latency_histogram(decisions),
            "stale": stale_rate(decisions),
        }

    @app.get("/api/report")
    def report() -> PlainTextResponse:
        files = sorted(report_dir.glob("*.md"))
        if not files:
            return PlainTextResponse("No analyst report yet.\n")
        return PlainTextResponse(files[-1].read_text(encoding="utf-8"))

    return app


def _last(path: Path) -> dict[str, Any] | None:
    rows = read_tail_jsonl(path, max_bytes=100_000)
    return rows[-1] if rows else None


def _stream_pnl(path: Path) -> dict[str, Any]:
    points: list[dict[str, float]] = []
    last: dict[str, Any] | None = None
    fills = 0
    count = 0
    for row in iter_jsonl(path):
        count += 1
        last = row
        for event in row.get("events") or []:
            kind = str(event.get("kind", ""))
            if kind.startswith("maker") or kind.startswith("taker"):
                fills += 1
        points.append(
            {
                "ts_ms": float(row["ts_ms"]),
                "equity": float(row.get("equity") or 0.0),
                "fees_paid": float(row.get("fees_paid") or 0.0),
                "gas_paid": float(row.get("gas_paid") or 0.0),
            }
        )
        if len(points) > 800:
            tail = points[-1]
            step = (len(points) - 2) / 398
            points = [points[0]] + [points[int(1 + i * step)] for i in range(398)] + [tail]
    summary = strategy_summary([last] if last else [])
    summary["fills"] = fills
    summary["blocks"] = count
    return {"summary": summary, "curve": points}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only paper-trading dashboard")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(Path(args.log_dir), Path(args.report_dir)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
