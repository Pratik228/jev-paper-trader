"""Clocks so replay deadlines use event time and live mode uses wall time."""

from __future__ import annotations

import time


class SimClock:
    def __init__(self, start_ms: int = 0) -> None:
        self.t = start_ms

    def now_ms(self) -> int:
        return self.t


class WallClock:
    def now_ms(self) -> int:
        return int(time.time() * 1000)
