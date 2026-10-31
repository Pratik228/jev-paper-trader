"""Nightly analyst. Writes a markdown post-mortem and a proposal diff.

The agent never writes config.yaml. Threshold changes stay in proposals/.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
from pathlib import Path
from typing import Any, TypedDict

from jev_paper.metrics import (
    STRATEGIES,
    calibration,
    latency_histogram,
    load_logs,
    regime_breakdown,
    stale_rate,
    strategy_summary,
)


class AnalystState(TypedDict, total=False):
    day: str
    log_dir: str
    config_text: str
    metrics: dict[str, Any]
    narrative: str
    narrative_source: str
    proposal: str


def run_analyst(
    *,
    day: str,
    log_dir: Path,
    report_dir: Path,
    proposal_dir: Path,
    config_path: Path,
) -> dict[str, Path]:
    config_before = config_path.read_bytes()
    state: AnalystState = {
        "day": day,
        "log_dir": str(log_dir),
        "config_text": config_path.read_text(encoding="utf-8"),
    }
    try:
        final = _run_langgraph(state)
        engine = "langgraph"
    except ImportError:
        final = _run_linear(state)
        engine = "linear"
    report_dir.mkdir(parents=True, exist_ok=True)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{day}.md"
    proposal_path = proposal_dir / f"{day}.diff"
    header = (
        f"<!-- analyst engine: {engine}; narrative: {final.get('narrative_source', 'template')} -->\n\n"
    )
    report_path.write_text(header + str(final.get("narrative") or ""), encoding="utf-8")
    proposal_path.write_text(str(final.get("proposal") or "no changes\n"), encoding="utf-8")
    if config_path.read_bytes() != config_before:
        raise RuntimeError("analyst must not edit config.yaml")
    return {"report": report_path, "proposal": proposal_path}


def load_day(state: AnalystState) -> AnalystState:
    logs = load_logs(Path(state["log_dir"]))
    summaries = {name: strategy_summary(logs[name]) for name in STRATEGIES}
    state["metrics"] = {
        "summaries": summaries,
        "calibration": calibration(logs["decisions"], logs["snapshots"]),
        "regimes": regime_breakdown(logs["decisions"], logs["outcomes"]),
        "latency": latency_histogram(logs["decisions"]),
        "stale": stale_rate(logs["decisions"]),
        "alerts": len(logs["alerts"]),
    }
    return state


def narrate(state: AnalystState) -> AnalystState:
    metrics = state.get("metrics") or {}
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        state["narrative"] = _template(state["day"], metrics)
        state["narrative_source"] = "template"
        return state
    try:
        import anthropic

        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=model,
            max_tokens=1200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write a short paper-trading post-mortem in markdown. "
                        "Use only these metrics. Do not invent fills or a live track record. "
                        "Do not propose editing files yourself.\n\n"
                        + _template(state["day"], metrics)
                    ),
                }
            ],
        )
        text = "".join(getattr(block, "text", "") for block in message.content)
        state["narrative"] = text or _template(state["day"], metrics)
        state["narrative_source"] = "claude"
    except Exception as exc:
        state["narrative"] = _template(state["day"], metrics) + f"\n\nClaude call failed: {exc.__class__.__name__}.\n"
        state["narrative_source"] = "template"
    return state


def propose(state: AnalystState) -> AnalystState:
    metrics = state.get("metrics") or {}
    changes = _suggested_changes(metrics)
    updated = _apply_line_changes(state.get("config_text") or "", changes)
    if updated == state.get("config_text"):
        state["proposal"] = f"# {state['day']}: no threshold change proposed\n"
        return state
    diff = difflib.unified_diff(
        (state.get("config_text") or "").splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile="config.yaml",
        tofile="config.yaml",
    )
    state["proposal"] = "".join(diff)
    return state


def _suggested_changes(metrics: dict[str, Any]) -> dict[str, str]:
    changes: dict[str, str] = {}
    for summary in (metrics.get("summaries") or {}).values():
        share = summary.get("fee_share_of_gross")
        if share is not None and share > 0.5 and summary.get("net_pnl", 0) < 0:
            changes["min_half_spread_bps"] = "3.0"
            changes["baseline_half_spread_bps"] = "8.0"
            break
    stale = (metrics.get("stale") or {}).get("rate")
    if stale is not None and stale > 0.1:
        changes["call_every_n_blocks"] = "10"
    return changes


def _apply_line_changes(text: str, changes: dict[str, str]) -> str:
    if not changes:
        return text

    def replace(match: re.Match[str]) -> str:
        key = match.group(2)
        if key not in changes:
            return match.group(0)
        return f"{match.group(1)}{key}: {changes[key]}"

    pattern = re.compile(r"^([ \t]*)([A-Za-z0-9_]+):[ \t]*.*$", re.MULTILINE)
    return pattern.sub(replace, text)


def _template(day: str, metrics: dict[str, Any]) -> str:
    lines = [f"# Paper post-mortem {day}", "", "This note is computed from the JSONL logs. It is not a live track record.", ""]
    lines.append("## PnL after fees")
    for name, summary in (metrics.get("summaries") or {}).items():
        share = summary.get("fee_share_of_gross")
        share_text = "n/a" if share is None else f"{share:.2%}"
        lines.append(
            f"- {name}: net {summary['net_pnl']:.4f}, gross {summary['gross_pnl']:.4f}, "
            f"fees {summary['fees_paid']:.4f}, gas {summary['gas_paid']:.4f}, "
            f"fee share of |gross| {share_text}, fills {summary['fills']}"
        )
    lines.extend(["", "## Calibration", ""])
    for bucket in metrics.get("calibration") or []:
        rate = "n/a" if bucket["hit_rate"] is None else f"{bucket['hit_rate']:.2%}"
        lines.append(f"- confidence {bucket['lo']:.1f}-{bucket['hi']:.1f}: n={bucket['n']} hit rate {rate}")
    lines.extend(["", "## Regimes", ""])
    regimes = metrics.get("regimes") or {}
    if not regimes:
        lines.append("- no regime tags")
    for name, slot in regimes.items():
        lines.append(f"- {name}: pnl {slot['pnl']:.4f} over {int(slot['blocks'])} blocks")
    stale = metrics.get("stale") or {}
    lines.extend(["", "## Latency and stale decisions", ""])
    rate = stale.get("rate")
    rate_text = "n/a" if rate is None else f"{rate:.2%}"
    lines.append(f"- stale rate {rate_text} on {stale.get('n', 0)} logged calls")
    for item in metrics.get("latency") or []:
        lines.append(f"- latency {item['bin']}: {item['n']}")
    lines.append("")
    lines.append("Proposed threshold edits, if any, are in the matching proposals diff and were not applied.")
    lines.append("")
    return "\n".join(lines)


def _run_linear(state: AnalystState) -> AnalystState:
    return propose(narrate(load_day(state)))


def _run_langgraph(state: AnalystState) -> AnalystState:
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AnalystState)
    graph.add_node("load", load_day)
    graph.add_node("narrate", narrate)
    graph.add_node("propose", propose)
    graph.add_edge(START, "load")
    graph.add_edge("load", "narrate")
    graph.add_edge("narrate", "propose")
    graph.add_edge("propose", END)
    return graph.compile().invoke(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a paper-trading post-mortem. Does not edit config.yaml.")
    parser.add_argument("--day", required=True)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--proposal-dir", default="proposals")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    paths = run_analyst(
        day=args.day,
        log_dir=Path(args.log_dir),
        report_dir=Path(args.report_dir),
        proposal_dir=Path(args.proposal_dir),
        config_path=Path(args.config),
    )
    print(paths["report"])
    print(paths["proposal"])


if __name__ == "__main__":
    main()
