"""Position, loss, drawdown, staleness, and API-error limits."""

from __future__ import annotations

from dataclasses import dataclass

from jev_paper.config import RiskConfig


@dataclass(frozen=True)
class RiskVerdict:
    allow_quotes: bool
    flatten: bool
    reason: str | None
    alert: bool
    reduce_only: bool = False


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        position_usd: float,
        daily_pnl: float,
        drawdown: float,
        data_age_ms: int | None,
        decision_latency_ms: float | None,
        consecutive_api_errors: int,
    ) -> RiskVerdict:
        if abs(position_usd) > self.config.max_position_usd + self.config.max_order_usd:
            return RiskVerdict(False, True, "max position", True, False)
        if abs(position_usd) > self.config.max_position_usd:
            return RiskVerdict(True, False, "max position", True, True)
        if daily_pnl <= -self.config.max_daily_loss_usd:
            return RiskVerdict(False, True, "daily loss", True)
        if drawdown >= self.config.max_drawdown_usd:
            return RiskVerdict(False, True, "drawdown", True)
        if consecutive_api_errors >= self.config.max_consecutive_api_errors:
            return RiskVerdict(False, True, "consecutive api errors", True)
        if data_age_ms is None or data_age_ms > self.config.max_data_age_ms:
            return RiskVerdict(False, False, "stale market data", True)
        if decision_latency_ms is not None and decision_latency_ms > self.config.max_decision_latency_ms:
            return RiskVerdict(False, False, "decision latency", False)
        return RiskVerdict(True, False, None, False)
