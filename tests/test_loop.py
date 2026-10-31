from __future__ import annotations

from pathlib import Path

from jev_paper.alerts.telegram import Alerter
from jev_paper.config import AppConfig, FeeConfig, JevConfig, PricingConfig, RiskConfig, StateConfig, default_config
from jev_paper.decision.jev_client import DecisionError, INVENTORY_LABELS, QUOTE_LABELS, MockClient, _choice, _score
from jev_paper.jsonlog import read_jsonl
from jev_paper.loop import run_replay
from jev_paper.types import BookEvent, BookLevel, Judgment, TradeEvent


def _config(**jev_over: object) -> AppConfig:
    cfg = default_config()
    cfg.state = StateConfig(ret_1m_ms=1000, ret_5m_ms=2000, ret_30m_ms=3000, vol_24h_ms=86_400_000, trade_window_ms=2000, vol_sample_ms=500)
    cfg.jev = JevConfig(client="mock", model="jev-1.14.0", call_every_n_blocks=1, timeout_s=0.8, block_ms=1000)
    cfg.risk = RiskConfig(max_decision_latency_ms=10_000, max_data_age_ms=10_000, max_consecutive_api_errors=5, quote_size=0.01, max_order_usd=150, max_position_usd=500, max_daily_loss_usd=25, max_drawdown_usd=40)
    cfg.fees = FeeConfig()
    cfg.pricing = PricingConfig()
    for key, value in jev_over.items():
        setattr(cfg.jev, key, value)
    return cfg


def _book(ts: int, mid: float = 100.0) -> BookEvent:
    return BookEvent(
        ts_ms=ts,
        venue="binance",
        pair="BTCUSDT",
        bids=(BookLevel(mid - 0.05, 2.0), BookLevel(mid - 0.2, 2.0), BookLevel(mid - 0.4, 2.0)),
        asks=(BookLevel(mid + 0.05, 2.0), BookLevel(mid + 0.2, 2.0), BookLevel(mid + 0.4, 2.0)),
        recv_ts_ms=ts + 2,
    )


def _events() -> list[BookEvent | TradeEvent]:
    return [
        _book(0),
        _book(1000),
        TradeEvent(1500, "binance", "BTCUSDT", 99.5, 0.02, "sell", recv_ts_ms=1502),
        _book(2000),
    ]


def test_replay_logs_three_strategies_and_charges_a_fill(tmp_path: Path) -> None:
    run_replay(_events(), _config(), tmp_path)
    for name in ("outcomes_jev_gated.jsonl", "outcomes_jev_ungated.jsonl", "outcomes_baseline.jsonl"):
        rows = read_jsonl(tmp_path / name)
        assert rows
        assert rows[-1]["strategy"] == name.removeprefix("outcomes_").removesuffix(".jsonl")
    decisions = read_jsonl(tmp_path / "decisions.jsonl")
    assert decisions
    assert all(row["model_version"] == "mock-1" for row in decisions)
    assert any(row["fees_paid"] > 0 or row["inventory"] > 0 for row in read_jsonl(tmp_path / "outcomes.jsonl"))


def test_stale_judgment_is_skipped(tmp_path: Path) -> None:
    client = MockClient(latency_ms=5_000)
    cfg = _config()
    cfg.risk.max_decision_latency_ms = 10_000
    run_replay(_events(), cfg, tmp_path, client=client)
    decisions = read_jsonl(tmp_path / "decisions.jsonl")
    assert decisions and all(row["stale"] for row in decisions)
    reasons = [row["decision"]["reason"] for row in read_jsonl(tmp_path / "outcomes_jev_gated.jsonl")]
    assert "stale judgment" in reasons


def test_jev_down_uses_code_only_then_flattens(tmp_path: Path) -> None:
    class Down:
        model_version = "jev-1.14.0"

        def judge(self, snapshot: object, *, deadline_ms: int, clock: object) -> object:
            raise DecisionError("unavailable", retryable=True)

    cfg = _config()
    cfg.risk.max_consecutive_api_errors = 2
    alerter = Alerter(tmp_path / "alerts.jsonl", token=None, chat_id=None)
    run_replay(_events(), cfg, tmp_path, client=Down(), alerter=alerter)  # type: ignore[arg-type]
    gated = read_jsonl(tmp_path / "outcomes_jev_gated.jsonl")
    assert any(row["decision"]["source"] == "code_only" for row in gated)
    assert gated[-1]["halted"] is True
    assert read_jsonl(tmp_path / "alerts.jsonl")


def test_crisis_kill_switch_alerts(tmp_path: Path) -> None:
    crisis = Judgment(
        regime=_choice("crisis", 0.95, ["trending", "mean_reverting", "high_vol", "crisis"]),
        direction=_choice("down", 0.95, ["up", "down", "neutral"]),
        toxic_flow=0.1,
        liquidity_stressed=0.1,
        quote_environment=_score(3.0, QUOTE_LABELS, 0.95),
        inventory_pressure=_score(0.0, INVENTORY_LABELS, 0.95),
        model_version="mock-1",
        latency_ms=1.0,
        stale=False,
    )
    client = MockClient(script={1: crisis})
    alerter = Alerter(tmp_path / "alerts.jsonl", token=None, chat_id=None)
    run_replay(_events(), _config(), tmp_path, client=client, alerter=alerter)
    assert read_jsonl(tmp_path / "outcomes_jev_ungated.jsonl")[-1]["halted"] is True
    assert read_jsonl(tmp_path / "outcomes_baseline.jsonl")[-1]["halted"] is False
    assert "flatten" in read_jsonl(tmp_path / "alerts.jsonl")[0]["text"]
