"""Load config.yaml. Every trading threshold lives here, not in prompts."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

import yaml

T = TypeVar("T")


@dataclass
class FeedConfig:
    venue: str = "binance"
    pair: str = "BTCUSDT"
    websocket_url: str = (
        "wss://stream.binance.com:9443/stream?streams=btcusdt@depth20@100ms/btcusdt@trade"
    )
    rest_depth_url: str = "https://api.binance.com/api/v3/depth"
    rest_trades_url: str = "https://api.binance.com/api/v3/trades"
    ws_fails_before_poll: int = 3
    poll_interval_s: float = 1.0
    reconnect_max_s: float = 30.0
    request_timeout_s: float = 5.0


@dataclass
class StateConfig:
    ret_1m_ms: int = 60_000
    ret_5m_ms: int = 300_000
    ret_30m_ms: int = 1_800_000
    vol_24h_ms: int = 86_400_000
    trade_window_ms: int = 60_000
    vol_sample_ms: int = 1_000


@dataclass
class JevConfig:
    client: str = "mock"
    model: str = "jev-1.14.0"
    call_every_n_blocks: int = 5
    timeout_s: float = 0.8
    block_ms: int = 1000


@dataclass
class PolicyConfig:
    min_choice_confidence: float = 0.55
    toxic_flow_pull: float = 0.72
    liquidity_stress_widen: float = 0.65
    quote_env_stand_down_max: float = 0.5
    quote_env_wide_max: float = 1.5
    inventory_skew_hard_min: float = 2.0
    inventory_reduce_min: float = 2.6
    direction_skew_bps: float = 1.5
    inventory_skew_bps: float = 4.0
    widen_multiplier: float = 1.75
    wide_multiplier: float = 2.5
    code_vol_ratio_stand_down: float = 2.5
    code_spread_bps_pull: float = 25.0
    code_imbalance_skew: float = 0.35


@dataclass
class PricingConfig:
    gamma: float = 0.1
    k: float = 1.5
    horizon_s: float = 60.0
    min_half_spread_bps: float = 2.0
    max_half_spread_bps: float = 40.0
    baseline_half_spread_bps: float = 6.0
    baseline_skew_bps_per_btc: float = 400.0
    sigma_floor: float = 1e-8
    requote_bps: float = 1.0


@dataclass
class RiskConfig:
    max_position_usd: float = 500.0
    max_daily_loss_usd: float = 25.0
    max_drawdown_usd: float = 40.0
    max_order_usd: float = 150.0
    max_data_age_ms: int = 5_000
    max_decision_latency_ms: int = 900
    max_consecutive_api_errors: int = 5
    quote_size: float = 0.01


@dataclass
class FeeConfig:
    venue: str = "binance"
    maker_bps: float = 10.0
    taker_bps: float = 10.0
    gas_per_event: float = 0.0
    charge_place: bool = False
    charge_cancel: bool = False
    charge_fill_gas: bool = False
    bnb_discount: bool = False


@dataclass
class AlertConfig:
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"


@dataclass
class HealthConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    feed: FeedConfig
    state: StateConfig
    jev: JevConfig
    policy: PolicyConfig
    pricing: PricingConfig
    risk: RiskConfig
    fees: FeeConfig
    alerts: AlertConfig
    health: HealthConfig
    log_dir: str = "logs"
    data_dir: str = "data"


def _fill(cls: type[T], raw: dict[str, Any] | None) -> T:
    data = raw or {}
    known = {item.name for item in fields(cls)}
    kwargs = {key: value for key, value in data.items() if key in known}
    return cls(**kwargs)


def load_config(path: str | Path) -> AppConfig:
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("config.yaml must be a mapping")
    fees = _fees(raw)
    return AppConfig(
        feed=_fill(FeedConfig, raw.get("feed")),
        state=_fill(StateConfig, raw.get("state")),
        jev=_fill(JevConfig, raw.get("jev")),
        policy=_fill(PolicyConfig, raw.get("policy")),
        pricing=_fill(PricingConfig, raw.get("pricing")),
        risk=_fill(RiskConfig, raw.get("risk")),
        fees=fees,
        alerts=_fill(AlertConfig, raw.get("alerts")),
        health=_fill(HealthConfig, raw.get("health")),
        log_dir=str(raw.get("log_dir", "logs")),
        data_dir=str(raw.get("data_dir", "data")),
    )


def _fees(raw: dict[str, Any]) -> FeeConfig:
    block = dict(raw.get("fees") or {})
    venue = str(block.get("venue") or (raw.get("feed") or {}).get("venue") or "binance")
    profiles = block.get("venues") or {}
    profile = dict(profiles.get(venue) or {})
    merged = {**profile, **{k: v for k, v in block.items() if k not in {"venues", "venue"}}}
    merged["venue"] = venue
    return _fill(FeeConfig, merged)


def default_config() -> AppConfig:
    return AppConfig(
        feed=FeedConfig(),
        state=StateConfig(),
        jev=JevConfig(),
        policy=PolicyConfig(),
        pricing=PricingConfig(),
        risk=RiskConfig(),
        fees=FeeConfig(),
        alerts=AlertConfig(),
        health=HealthConfig(),
        data_dir="data",
    )
