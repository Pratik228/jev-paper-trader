"""Offline metrics shared by the analyst, the dashboard, and the notebook."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jev_paper.jsonlog import read_jsonl

STRATEGIES = ("jev_gated", "jev_ungated", "baseline")
LATENCY_BINS = (50, 100, 250, 500, 1000)


def load_logs(log_dir: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "snapshots": read_jsonl(log_dir / "snapshots.jsonl"),
        "decisions": read_jsonl(log_dir / "decisions.jsonl"),
        "outcomes": read_jsonl(log_dir / "outcomes.jsonl"),
        "alerts": read_jsonl(log_dir / "alerts.jsonl"),
        **{name: read_jsonl(log_dir / f"outcomes_{name}.jsonl") for name in STRATEGIES},
    }


def strategy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "blocks": 0,
            "net_pnl": 0.0,
            "fees_paid": 0.0,
            "gas_paid": 0.0,
            "gross_pnl": 0.0,
            "fee_share_of_gross": None,
            "fills": 0,
        }
    last = rows[-1]
    fees = float(last.get("fees_paid") or 0.0)
    gas = float(last.get("gas_paid") or 0.0)
    net = float(last.get("equity") or 0.0)
    gross = net + fees + gas
    share = (fees + gas) / abs(gross) if gross else None
    fills = 0
    for row in rows:
        for event in row.get("events") or []:
            if str(event.get("kind", "")).startswith("maker") or str(event.get("kind", "")).startswith("taker"):
                fills += 1
    return {
        "blocks": len(rows),
        "net_pnl": net,
        "fees_paid": fees,
        "gas_paid": gas,
        "gross_pnl": gross,
        "fee_share_of_gross": share,
        "fills": fills,
        "inventory": last.get("inventory"),
        "halted": last.get("halted"),
    }


def equity_curve(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    curve = []
    for row in rows:
        curve.append(
            {
                "ts_ms": float(row["ts_ms"]),
                "equity": float(row.get("equity") or 0.0),
                "fees_paid": float(row.get("fees_paid") or 0.0),
                "gas_paid": float(row.get("gas_paid") or 0.0),
            }
        )
    return curve


def calibration(
    decisions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    horizon_ms: int = 60_000,
) -> list[dict[str, Any]]:
    mids = _mid_path(snapshots)
    buckets = [{"lo": i / 5, "hi": (i + 1) / 5, "n": 0, "hits": 0} for i in range(5)]
    seen: set[int] = set()
    for decision in decisions:
        block_id = int(decision.get("block_id", -1))
        if block_id in seen:
            continue
        seen.add(block_id)
        if decision.get("stale") or decision.get("error"):
            continue
        direction = (decision.get("direction") or {}).get("choice")
        if direction not in {"up", "down"}:
            continue
        confidence = float((decision.get("direction") or {}).get("confidence") or 0.0)
        future = _forward_return(mids, int(decision["ts_ms"]), horizon_ms)
        if future is None or future == 0:
            continue
        hit = (future > 0 and direction == "up") or (future < 0 and direction == "down")
        index = min(int(confidence * 5), 4)
        buckets[index]["n"] += 1
        buckets[index]["hits"] += int(hit)
    for bucket in buckets:
        bucket["hit_rate"] = (bucket["hits"] / bucket["n"]) if bucket["n"] else None
    return buckets


def regime_breakdown(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    regime_at: dict[tuple[str, int], str] = {}
    for decision in decisions:
        regime = (decision.get("regime") or {}).get("choice")
        if regime and not decision.get("stale") and not decision.get("error"):
            regime_at[(str(decision.get("strategy")), int(decision["block_id"]))] = str(regime)
    totals: dict[str, dict[str, float]] = {}
    last_equity: dict[str, float] = {}
    carried: dict[str, str] = {}
    for row in outcomes:
        name = str(row.get("strategy"))
        block = int(row["block_id"])
        equity = float(row.get("equity") or 0.0)
        previous = last_equity.get(name, equity)
        delta = equity - previous
        last_equity[name] = equity
        regime = regime_at.get((name, block), carried.get(name, "no_judgment"))
        if (name, block) in regime_at:
            carried[name] = regime
        slot = totals.setdefault(regime, {"pnl": 0.0, "blocks": 0.0})
        slot["pnl"] += delta
        slot["blocks"] += 1
    return totals


def latency_histogram(decisions: list[dict[str, Any]]) -> list[dict[str, float | int | str]]:
    counts = [0 for _ in range(len(LATENCY_BINS) + 1)]
    for decision in decisions:
        if "latency_ms" not in decision or decision.get("latency_ms") is None:
            continue
        latency = float(decision["latency_ms"])
        placed = False
        for index, edge in enumerate(LATENCY_BINS):
            if latency < edge:
                counts[index] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    labels = [f"<{edge}" for edge in LATENCY_BINS] + [f">={LATENCY_BINS[-1]}"]
    return [{"bin": label, "n": count} for label, count in zip(labels, counts)]


def stale_rate(decisions: list[dict[str, Any]]) -> dict[str, float | int | None]:
    if not decisions:
        return {"n": 0, "stale": 0, "rate": None}
    stale = sum(1 for decision in decisions if decision.get("stale"))
    return {"n": len(decisions), "stale": stale, "rate": stale / len(decisions)}


def _mid_path(snapshots: list[dict[str, Any]]) -> list[tuple[int, float]]:
    seen: dict[int, float] = {}
    for row in snapshots:
        mid = row.get("mid")
        if mid is None:
            continue
        seen[int(row["ts_ms"])] = float(mid)
    return sorted(seen.items())


def _forward_return(mids: list[tuple[int, float]], ts_ms: int, horizon_ms: int) -> float | None:
    now = _mid_at(mids, ts_ms)
    later = _mid_at(mids, ts_ms + horizon_ms)
    if now is None or later is None or now == 0:
        return None
    if mids and mids[-1][0] < ts_ms + horizon_ms:
        return None
    return later / now - 1.0


def _mid_at(mids: list[tuple[int, float]], ts_ms: int) -> float | None:
    found: float | None = None
    for stamp, mid in mids:
        if stamp > ts_ms:
            break
        found = mid
    return found
