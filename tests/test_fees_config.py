from __future__ import annotations

from pathlib import Path

from jev_paper.config import load_config


def test_binance_profile_has_no_gas() -> None:
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    assert cfg.fees.venue == "binance"
    assert cfg.fees.maker_bps == 10.0
    assert cfg.fees.taker_bps == 10.0
    assert cfg.fees.gas_per_event == 0.0
    assert cfg.fees.charge_place is False
    assert cfg.fees.charge_cancel is False
    assert cfg.fees.charge_fill_gas is False
