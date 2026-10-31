"""Write calibration, fee, and latency charts from a log directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from jev_paper.metrics import STRATEGIES, calibration, equity_curve, latency_histogram, load_logs, stale_rate, strategy_summary


def write_figures(log_dir: Path, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    logs = load_logs(log_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name in STRATEGIES:
        curve = equity_curve(logs[name])
        if not curve:
            continue
        start = curve[0]["ts_ms"]
        ax.plot([(point["ts_ms"] - start) / 60_000.0 for point in curve], [point["equity"] for point in curve], label=name)
    ax.set_title("Paper equity after fees and gas")
    ax.set_xlabel("minutes from tape start")
    ax.set_ylabel("equity (quote currency)")
    ax.legend()
    fig.tight_layout()
    path = out_dir / "pnl.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    written.append(path)

    buckets = calibration(logs["decisions"], logs["snapshots"])
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = [f"{bucket['lo']:.1f}-{bucket['hi']:.1f}" for bucket in buckets]
    rates = [0.0 if bucket["hit_rate"] is None else bucket["hit_rate"] for bucket in buckets]
    ax.bar(labels, rates)
    ax.axhline(0.5, color="gray", linewidth=1)
    ax.set_ylim(0, 1)
    ax.set_title("Direction hit rate by confidence bucket")
    ax.set_ylabel("hit rate")
    fig.tight_layout()
    path = out_dir / "calibration.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    written.append(path)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    names = []
    shares = []
    for name in STRATEGIES:
        summary = strategy_summary(logs[name])
        names.append(name)
        share = summary["fee_share_of_gross"]
        shares.append(0.0 if share is None else share)
    ax.bar(names, shares)
    ax.set_title("Fees plus gas as a fraction of absolute gross PnL")
    ax.set_ylabel("share")
    fig.tight_layout()
    path = out_dir / "fee_share.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    written.append(path)

    hist = latency_histogram(logs["decisions"])
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([str(item["bin"]) for item in hist], [int(item["n"]) for item in hist])
    rate = stale_rate(logs["decisions"])
    ax.set_title(f"Judgment latency (stale rate {rate['rate']})")
    ax.set_ylabel("calls")
    fig.tight_layout()
    path = out_dir / "latency.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Chart a paper-log directory")
    parser.add_argument("--log-dir", default="logs/sample")
    parser.add_argument("--out-dir", default="reports/sample")
    args = parser.parse_args()
    paths = write_figures(Path(args.log_dir), Path(args.out_dir))
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
