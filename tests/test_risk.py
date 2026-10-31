from __future__ import annotations

from jev_paper.config import RiskConfig
from jev_paper.risk.engine import RiskEngine


def test_hard_limits_flatten_and_soft_limits_veto() -> None:
    engine = RiskEngine(RiskConfig(max_daily_loss_usd=10, max_position_usd=100, max_data_age_ms=1000, max_decision_latency_ms=50, max_consecutive_api_errors=3))
    loss = engine.evaluate(position_usd=1, daily_pnl=-10, drawdown=0, data_age_ms=10, decision_latency_ms=1, consecutive_api_errors=0)
    assert loss.flatten and loss.alert
    stale = engine.evaluate(position_usd=1, daily_pnl=0, drawdown=0, data_age_ms=5000, decision_latency_ms=1, consecutive_api_errors=0)
    assert stale.allow_quotes is False and stale.flatten is False
    slow = engine.evaluate(position_usd=1, daily_pnl=0, drawdown=0, data_age_ms=10, decision_latency_ms=80, consecutive_api_errors=0)
    assert slow.reason == "decision latency" and slow.flatten is False
    errors = engine.evaluate(position_usd=1, daily_pnl=0, drawdown=0, data_age_ms=10, decision_latency_ms=1, consecutive_api_errors=3)
    assert errors.flatten and errors.reason == "consecutive api errors"
    slight = engine.evaluate(position_usd=120, daily_pnl=0, drawdown=0, data_age_ms=10, decision_latency_ms=1, consecutive_api_errors=0)
    assert slight.reduce_only and slight.flatten is False
    breach = engine.evaluate(position_usd=400, daily_pnl=0, drawdown=0, data_age_ms=10, decision_latency_ms=1, consecutive_api_errors=0)
    assert breach.flatten and breach.reason == "max position"
    ok = engine.evaluate(position_usd=1, daily_pnl=0, drawdown=0, data_age_ms=10, decision_latency_ms=1, consecutive_api_errors=0)
    assert ok.allow_quotes and ok.reason is None and ok.reduce_only is False
