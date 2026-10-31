from __future__ import annotations

from types import SimpleNamespace

import pytest

from jev_paper.clock import SimClock
from jev_paper.decision.jev_client import (
    BATTERY,
    DecisionError,
    JevClient,
    MockClient,
    _from_response,
)
from jev_paper.types import AccountView, Snapshot


def _snap(block_id: int = 1) -> Snapshot:
    return Snapshot(
        block_id=block_id,
        ts_ms=1_000,
        mid=100.0,
        microprice=100.0,
        spread_bps=2.0,
        depth_bid=[1.0, 1.0, 1.0],
        depth_ask=[1.0, 1.0, 1.0],
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
        latency_ms_last10=[1.0],
        warmup=False,
    )


def test_prompts_contain_no_thresholds() -> None:
    for spec in BATTERY.values():
        assert not any(char.isdigit() for char in spec["instructions"])
        criteria = spec.get("criteria")
        if isinstance(criteria, dict):
            blobs = list(criteria) + [str(value) for value in criteria.values()]
        elif isinstance(criteria, list):
            blobs = criteria
        else:
            blobs = []
        for blob in blobs:
            assert not any(char.isdigit() for char in blob)


def test_mock_is_deterministic_and_stale_after_deadline() -> None:
    clock = SimClock(1_000)
    first = MockClient().judge(_snap(), deadline_ms=2_000, clock=clock)
    second = MockClient().judge(_snap(), deadline_ms=2_000, clock=clock)
    assert first.to_dict() == second.to_dict()
    assert first.stale is False
    assert first.model_version == "mock-1"
    late = MockClient(latency_ms=50).judge(_snap(), deadline_ms=1_010, clock=clock)
    assert late.stale is True


def test_parse_sdk_shaped_response() -> None:
    response = SimpleNamespace(
        model="jev-1.14.0",
        choices={
            "regime": SimpleNamespace(choice="trending", confidence=0.8, probabilities={"trending": 0.8}),
            "direction": SimpleNamespace(choice="up", confidence=0.7, probabilities={"up": 0.7}),
        },
        nouls={
            "toxic_flow": SimpleNamespace(noul=0.2),
            "liquidity_stressed": SimpleNamespace(noul=0.1),
        },
        scores={
            "quote_environment": SimpleNamespace(score=2.2, confidence=0.6, probabilities={"Standard": 0.7}),
            "inventory_pressure": SimpleNamespace(score=0.2, confidence=0.6, probabilities={"None": 0.8}),
        },
        model_dump=lambda: {"model": "jev-1.14.0"},
    )
    judgment = _from_response(response, requested_model="jev-1.14.0", latency_ms=12.0, stale=False)
    assert judgment.model_version == "jev-1.14.0"
    assert judgment.regime is not None and judgment.regime.choice == "trending"
    assert judgment.toxic_flow == 0.2
    assert judgment.quote_environment is not None and judgment.quote_environment.label == "Standard"
    assert judgment.latency_ms == 12.0


def test_jev_client_reports_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "typesafe_sdk":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    client = JevClient("jev-1.14.0", timeout_s=0.5)
    with pytest.raises(DecisionError) as caught:
        client.judge(_snap(), deadline_ms=5_000, clock=SimClock(1_000))
    assert caught.value.retryable is False


def test_account_view_unused_import_guard() -> None:
    assert AccountView(0, 0, 0).inventory == 0
