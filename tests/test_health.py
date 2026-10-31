import json
from pathlib import Path
from urllib.request import Request, urlopen

from jev_paper.alerts.telegram import Alerter
from jev_paper.health import HealthState, serve


def test_health_endpoint() -> None:
    state = HealthState()
    state.update({"ok": True, "ready": True, "client": "mock"})
    server = serve(state, "127.0.0.1", 0)
    port = server.server_address[1]
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            body = json.loads(response.read().decode())
        assert body["ready"] is True
        assert body["client"] == "mock"
    finally:
        server.shutdown()


def test_telegram_does_not_log_the_token(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    class Response:
        def close(self) -> None:
            return None

    def opener(request: Request, timeout: float = 0) -> Response:
        seen["url"] = request.full_url
        return Response()

    alerter = Alerter(tmp_path / "alerts.jsonl", token="SECRETTOKEN", chat_id="42", opener=opener)
    alerter.send("jev_gated flatten: daily loss")
    logged = (tmp_path / "alerts.jsonl").read_text(encoding="utf-8")
    assert "SECRETTOKEN" not in logged
    assert "daily loss" in logged
    assert seen["url"].endswith("/sendMessage")
    assert "SECRETTOKEN" in seen["url"]
