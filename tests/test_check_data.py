from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path


def test_check_data_counts(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_data.py"
    spec = importlib.util.spec_from_file_location("check_data", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = tmp_path / "raw"
    raw.mkdir()
    with gzip.open(raw / "2026-10-30.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "book", "ts_ms": 1_000}) + "\n")
        handle.write(json.dumps({"kind": "trade", "ts_ms": 3_600_000}) + "\n")
    (tmp_path / "gaps.jsonl").write_text(json.dumps({"kind": "disconnect", "gap_ms": 5_000}) + "\n")
    summary = module.summarize(tmp_path)
    assert summary["event_total"] == 2
    assert summary["gap_count"] == 1
    assert summary["days"][0]["books"] == 1
    assert summary["days"][0]["trades"] == 1
    assert "hours" in summary["days"][0]
    text = module.render(summary)
    assert "2026-10-30" in text
