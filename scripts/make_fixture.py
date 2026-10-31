"""Deterministic public-book fixture. No network."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path


def generate(minutes: int, seed: int = 0) -> list[dict[str, object]]:
    rng = random.Random(seed)
    mid = 100_000.0
    start = 1_700_000_000_000
    events: list[dict[str, object]] = []
    steps = max(minutes, 1) * 60
    for index in range(steps):
        ts = start + index * 1000
        mid *= math.exp(rng.gauss(0, 0.00008))
        half = mid * 0.0001
        events.append(
            {
                "kind": "book",
                "ts_ms": ts,
                "recv_ts_ms": ts + 4,
                "venue": "binance",
                "pair": "BTCUSDT",
                "bids": [
                    {"price": mid - half * (1 + level), "qty": 0.8 + level}
                    for level in range(3)
                ],
                "asks": [
                    {"price": mid + half * (1 + level), "qty": 0.7 + level}
                    for level in range(3)
                ],
            }
        )
        if index % 2:
            continue
        roll = rng.random()
        if roll < 0.15:
            price, aggressor = mid * (1 - 0.003), "sell"
        elif roll < 0.30:
            price, aggressor = mid * (1 + 0.003), "buy"
        else:
            price, aggressor = mid, "buy" if rng.random() < 0.5 else "sell"
        events.append(
            {
                "kind": "trade",
                "ts_ms": ts + 200,
                "recv_ts_ms": ts + 210,
                "venue": "binance",
                "pair": "BTCUSDT",
                "price": price,
                "qty": 0.02,
                "aggressor": aggressor,
            }
        )
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--minutes", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in generate(args.minutes, args.seed):
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
