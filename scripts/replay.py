"""Replay recorded tapes through the three paper strategies."""

from __future__ import annotations

import argparse
from pathlib import Path

from jev_paper.config import load_config
from jev_paper.data.feed import read_events
from jev_paper.loop import run_replay


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay gzip JSONL recordings (or a synthetic fixture) through all three strategies."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--tape", default="data/raw", help="File or directory of .jsonl / .jsonl.gz")
    parser.add_argument("--log-dir", default="logs/replay")
    args = parser.parse_args()
    config = load_config(args.config)
    events = list(read_events(args.tape))
    if not events:
        raise SystemExit(f"no events in {args.tape}")
    run_replay(events, config, Path(args.log_dir))
    print(f"replayed {len(events)} events from {args.tape} into {args.log_dir}")


if __name__ == "__main__":
    main()
