from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jev_paper.dashboard.app import create_app
from jev_paper.jsonlog import append_jsonl


def test_dashboard_is_read_only_and_serves_logs(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    append_jsonl(
        log_dir / "outcomes_baseline.jsonl",
        {
            "strategy": "baseline",
            "block_id": 2,
            "ts_ms": 2_000,
            "equity": -0.02,
            "fees_paid": 0.0,
            "gas_paid": 0.02,
            "inventory": 0.0,
            "events": [],
            "decision": {"action": "QUOTE_BOTH_SIDES", "reason": "code book rules", "source": "code_only"},
            "mid": 100.0,
        },
    )
    append_jsonl(
        log_dir / "snapshots.jsonl",
        {"strategy": "baseline", "block_id": 2, "ts_ms": 2_000, "mid": 100.0, "spread_bps": 2.0, "imbalance": 0.1},
    )
    append_jsonl(
        log_dir / "decisions.jsonl",
        {"strategy": "baseline", "block_id": 2, "ts_ms": 2_000, "stale": False, "latency_ms": 4, "model_version": "mock-1"},
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-10-30.md").write_text("# sample report\n", encoding="utf-8")
    app = create_app(log_dir, reports)
    for route in app.routes:
        methods = getattr(route, "methods", set())
        assert methods <= {"GET", "HEAD"}
    client = TestClient(app)
    live = client.get("/api/live")
    assert live.status_code == 200
    assert live.json()["snapshot"]["mid"] == 100.0
    pnl = client.get("/api/pnl").json()
    assert pnl["strategies"]["baseline"]["summary"]["gas_paid"] == 0.02
    assert client.get("/api/report").text.startswith("# sample report")
    page = client.get("/")
    assert page.status_code == 200
    assert "cannot place" in page.text
    assert client.post("/api/pnl").status_code == 405
