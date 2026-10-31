from __future__ import annotations

from jev_paper.config import PolicyConfig
from jev_paper.decision.jev_client import INVENTORY_LABELS, QUOTE_LABELS, _choice, _score
from jev_paper.policy.engine import PolicyEngine
from jev_paper.types import Judgment, Snapshot


def _snap(**overrides: float) -> Snapshot:
    base = dict(
        block_id=1,
        ts_ms=1,
        mid=100.0,
        microprice=100.0,
        spread_bps=2.0,
        depth_bid=[1.0],
        depth_ask=[1.0],
        imbalance=0.0,
        ret_1m=0.0,
        ret_5m=0.0,
        ret_30m=0.0,
        aggressive_buy_ratio=0.5,
        realized_vol_5m=0.001,
        realized_vol_30m=0.001,
        vol_vs_24h=1.0,
        inventory=0.0,
        unrealized_pnl=0.0,
        drawdown=0.0,
        latency_ms_last10=[],
        warmup=False,
    )
    base.update(overrides)
    return Snapshot(**base)  # type: ignore[arg-type]


def _judgment(**overrides: object) -> Judgment:
    judgment = Judgment(
        regime=_choice("mean_reverting", 0.9, ["trending", "mean_reverting", "high_vol", "crisis"]),
        direction=_choice("neutral", 0.9, ["up", "down", "neutral"]),
        toxic_flow=0.1,
        liquidity_stressed=0.1,
        quote_environment=_score(2.5, QUOTE_LABELS, 0.9),
        inventory_pressure=_score(0.0, INVENTORY_LABELS, 0.9),
        model_version="mock-1",
        latency_ms=1.0,
        stale=False,
    )
    for key, value in overrides.items():
        setattr(judgment, key, value)
    return judgment


def test_policy_uses_config_thresholds() -> None:
    strict = PolicyEngine(PolicyConfig(toxic_flow_pull=0.3))
    loose = PolicyEngine(PolicyConfig(toxic_flow_pull=0.95))
    snap = _snap()
    toxic = _judgment(toxic_flow=0.5)
    assert strict.from_judgment(snap, toxic, gated=False).action.value == "PULL_QUOTES"
    assert loose.from_judgment(snap, toxic, gated=False).action.value == "QUOTE_BOTH_SIDES"


def test_confidence_gate_falls_back() -> None:
    engine = PolicyEngine(PolicyConfig())
    quiet = _judgment(toxic_flow=0.99)
    quiet.regime = _choice("trending", 0.2, ["trending", "mean_reverting", "high_vol", "crisis"])
    gated = engine.from_judgment(_snap(), quiet, gated=True)
    ungated = engine.from_judgment(_snap(), quiet, gated=False)
    assert gated.source == "code_only"
    assert ungated.action.value == "PULL_QUOTES"


def test_crisis_kills_and_code_only_respects_spread() -> None:
    engine = PolicyEngine(PolicyConfig())
    crisis = _judgment()
    crisis.regime = _choice("crisis", 0.99, ["trending", "mean_reverting", "high_vol", "crisis"])
    assert engine.from_judgment(_snap(), crisis, gated=False).action.value == "KILL"
    assert engine.code_only(_snap(warmup=True)).action.value == "STAND_DOWN"
    assert engine.code_only(_snap(spread_bps=40)).action.value == "PULL_QUOTES"
