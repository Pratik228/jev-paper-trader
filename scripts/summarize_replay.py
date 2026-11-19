"""Summarize a --per-day replay: one row per day per strategy, plus totals.

Streams the outcome logs (they are large), keeps only what the table needs,
and writes results.json, results.md and an equity chart.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jev_paper.metrics import STRATEGIES


def _day_summary(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: dict[str, Any] | None = None
    first_ts = None
    fills = maker = taker = 0
    peak = 0.0
    max_dd = 0.0
    curve: list[tuple[int, float]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            first_ts = first_ts or row["ts_ms"]
            for event in row.get("events") or []:
                kind = str(event.get("kind", ""))
                if kind.startswith("maker"):
                    fills += 1
                    maker += 1
                elif kind.startswith("taker"):
                    fills += 1
                    taker += 1
            equity = float(row.get("equity") or 0.0)
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
            if row["block_id"] % 60 == 0:
                curve.append((row["ts_ms"], equity))
            last = row
    if last is None:
        return None
    fees = float(last.get("fees_paid") or 0.0)
    net = float(last.get("equity") or 0.0)
    return {
        "hours": round((last["ts_ms"] - first_ts) / 3_600_000, 2),
        "net_pnl": net,
        "gross_pnl": net + fees,
        "fees_paid": fees,
        "fills": fills,
        "maker_fills": maker,
        "taker_fills": taker,
        "max_drawdown": max_dd,
        "halted": bool(last.get("halted")),
        "end_inventory": last.get("inventory"),
        "curve": curve,
    }


def summarize(root: Path) -> dict[str, Any]:
    days = sorted(p for p in root.iterdir() if p.is_dir())
    out: dict[str, Any] = {"days": {}, "totals": {}}
    for day in days:
        out["days"][day.name] = {
            name: _day_summary(day / f"outcomes_{name}.jsonl") for name in STRATEGIES
        }
    for name in STRATEGIES:
        rows = [d[name] for d in out["days"].values() if d.get(name)]
        out["totals"][name] = {
            "days": len(rows),
            "net_pnl": sum(r["net_pnl"] for r in rows),
            "gross_pnl": sum(r["gross_pnl"] for r in rows),
            "fees_paid": sum(r["fees_paid"] for r in rows),
            "fills": sum(r["fills"] for r in rows),
            "maker_fills": sum(r["maker_fills"] for r in rows),
            "taker_fills": sum(r["taker_fills"] for r in rows),
            "green_days": sum(1 for r in rows if r["net_pnl"] > 0),
            "halted_days": sum(1 for r in rows if r["halted"]),
            "worst_day": min((r["net_pnl"] for r in rows), default=0.0),
            "best_day": max((r["net_pnl"] for r in rows), default=0.0),
        }
    return out


def to_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "| Strategy | Days | Net after fees | Gross before fees | Fees | Fills (maker/taker) | Green days | Days halted | Worst day | Best day |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, t in summary["totals"].items():
        lines.append(
            f"| {name} | {t['days']} | {t['net_pnl']:+.2f} | {t['gross_pnl']:+.2f} | {t['fees_paid']:.2f} | "
            f"{t['fills']} ({t['maker_fills']}/{t['taker_fills']}) | {t['green_days']} | {t['halted_days']} | "
            f"{t['worst_day']:+.2f} | {t['best_day']:+.2f} |"
        )
    lines += ["", "| Day | " + " | ".join(STRATEGIES) + " |", "| --- |" + " ---: |" * len(STRATEGIES)]
    for day, rows in summary["days"].items():
        cells = []
        for name in STRATEGIES:
            r = rows.get(name)
            cells.append("—" if not r else f"{r['net_pnl']:+.2f}{' (halt)' if r['halted'] else ''}")
        lines.append(f"| {day} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def chart(summary: dict[str, Any], path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5))
    for name in STRATEGIES:
        offset = 0.0
        xs: list[float] = []
        ys: list[float] = []
        for rows in summary["days"].values():
            r = rows.get(name)
            if not r:
                continue
            for ts, eq in r["curve"]:
                xs.append(ts / 86_400_000)
                ys.append(offset + eq)
            offset += r["net_pnl"]
        if xs:
            start = xs[0]
            ax.plot([x - start for x in xs], ys, label=f"{name} (mock answers)" if name.startswith("jev_") else name, linewidth=1.2)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("days")
    ax.set_ylabel("USDT")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs/realtape")
    parser.add_argument("--out-dir", default="reports/realtape")
    parser.add_argument("--title", default="Cumulative paper PnL after fees, real BTCUSDT tape (each day a fresh session)")
    args = parser.parse_args()
    summary = summarize(Path(args.log_dir))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slim = {"totals": summary["totals"], "days": {d: {n: ({k: v for k, v in r.items() if k != "curve"} if r else None) for n, r in rows.items()} for d, rows in summary["days"].items()}}
    (out / "results.json").write_text(json.dumps(slim, indent=2))
    (out / "results.md").write_text(to_markdown(summary))
    chart(summary, out / "pnl.png", args.title)
    print(to_markdown(summary))


if __name__ == "__main__":
    main()
