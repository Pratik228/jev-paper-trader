from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"


def test_source_has_no_live_order_path() -> None:
    import re

    banned = (
        r"api/v3/order",
        r"fapi/v1/order",
        r"private_key",
        r"BEGIN PRIVATE",
        r"create_order",
        r"\bwithdraw\b",
    )
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if re.search(token, text):
                offenders.append(f"{path.name}: {token}")
    assert offenders == []
