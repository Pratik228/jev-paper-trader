"""Telegram alerts for kill-switch and crash events. Missing credentials only log locally."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from jev_paper.jsonlog import append_jsonl


class Alerter:
    def __init__(
        self,
        path: Path,
        *,
        token: str | None,
        chat_id: str | None,
        opener: Callable[..., Any] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.path = path
        self.token = token or None
        self.chat_id = chat_id or None
        self._opener = opener or urlopen
        self._now_ms = now_ms or _wall_ms

    def send(self, text: str) -> None:
        record: dict[str, Any] = {"ts_ms": self._now_ms(), "text": text, "delivered": False}
        if self.token and self.chat_id:
            try:
                self._post(text)
                record["delivered"] = True
            except Exception as exc:
                record["delivery_error"] = exc.__class__.__name__
        append_jsonl(self.path, record)

    def _post(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": text}).encode("utf-8")
        request = Request(url, data=body, headers={"Content-Type": "application/json"})
        response = self._opener(request, timeout=5)
        close = getattr(response, "close", None)
        if close:
            close()


def _wall_ms() -> int:
    import time

    return int(time.time() * 1000)


def alerter_from_env(path: Path, token_env: str, chat_env: str) -> Alerter:
    return Alerter(path, token=os.environ.get(token_env), chat_id=os.environ.get(chat_env))
