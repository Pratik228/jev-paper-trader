from __future__ import annotations

import json
from pathlib import Path

from jev_paper.agent.analyst import run_analyst
from jev_paper.jsonlog import append_jsonl


def test_analyst_writes_report_and_does_not_edit_config(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    for name in ("jev_gated", "jev_ungated", "baseline"):
        append_jsonl(
            log_dir / f"outcomes_{name}.jsonl",
            {
                "strategy": name,
                "block_id": 1,
                "ts_ms": 1_000,
                "equity": -2.0,
                "fees_paid": 0.4,
                "gas_paid": 0.8,
                "events": [{"kind": "maker_buy", "fee": 0.4, "gas": 0.01, "qty": 0.01, "price": 100}],
                "inventory": 0.0,
                "halted": False,
                "mid": 100.0,
            },
        )
    append_jsonl(
        log_dir / "decisions.jsonl",
        {
            "strategy": "jev_gated",
            "block_id": 1,
            "ts_ms": 1_000,
            "stale": False,
            "latency_ms": 15,
            "model_version": "mock-1",
            "regime": {"choice": "mean_reverting", "confidence": 0.8, "probabilities": {}},
            "direction": {"choice": "up", "confidence": 0.8, "probabilities": {}},
        },
    )
    append_jsonl(log_dir / "snapshots.jsonl", {"strategy": "baseline", "block_id": 1, "ts_ms": 1_000, "mid": 100.0})
    append_jsonl(log_dir / "snapshots.jsonl", {"strategy": "baseline", "block_id": 2, "ts_ms": 61_000, "mid": 101.0})
    config = tmp_path / "config.yaml"
    config.write_text("pricing:\n  min_half_spread_bps: 2.0\n  baseline_half_spread_bps: 6.0\njev:\n  call_every_n_blocks: 5\n", encoding="utf-8")
    before = config.read_bytes()
    paths = run_analyst(
        day="2026-10-30",
        log_dir=log_dir,
        report_dir=tmp_path / "reports",
        proposal_dir=tmp_path / "proposals",
        config_path=config,
    )
    assert config.read_bytes() == before
    report = paths["report"].read_text(encoding="utf-8")
    assert "jev_gated" in report
    assert "not a live track record" in report
    proposal = paths["proposal"].read_text(encoding="utf-8")
    assert "min_half_spread_bps: 3.0" in proposal
    assert json.loads(json.dumps({"ok": True}))["ok"]
