"""Replay recorded tapes through the paper strategies.

With --per-day, every UTC day in the tape directory is an independent session
(fresh accounts, fresh state engine), so a daily-loss halt on one day does not
silence the rest of the run. Days run in parallel and events are streamed, so
a multi-week tape never sits in memory.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from jev_paper.config import load_config
from jev_paper.data.feed import read_events
from jev_paper.loop import run_replay


def _day_files(tape: Path) -> list[Path]:
    files = sorted(tape.glob("*.jsonl.gz")) + sorted(tape.glob("*.jsonl"))
    return sorted(files, key=lambda path: path.name)


def _replay_one(config_path: str, tape: str, log_dir: str) -> str:
    config = load_config(config_path)
    run_replay(read_events(tape), config, Path(log_dir))
    return log_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay gzip JSONL recordings (or a synthetic fixture) through every strategy."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--tape", default="data/raw", help="File or directory of .jsonl / .jsonl.gz")
    parser.add_argument("--log-dir", default="logs/replay")
    parser.add_argument("--per-day", action="store_true", help="One independent session per day file")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    tape = Path(args.tape)
    if not args.per_day:
        _replay_one(args.config, args.tape, args.log_dir)
        print(f"replayed {args.tape} into {args.log_dir}")
        return

    if not tape.is_dir():
        raise SystemExit("--per-day needs a directory of day files")
    days = _day_files(tape)
    if not days:
        raise SystemExit(f"no day files in {tape}")
    root = Path(args.log_dir)
    jobs = {day.name.split(".")[0]: day for day in days}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_replay_one, args.config, str(path), str(root / name)) for name, path in jobs.items()]
        for future in futures:
            print(f"done {future.result()}", flush=True)


if __name__ == "__main__":
    main()
