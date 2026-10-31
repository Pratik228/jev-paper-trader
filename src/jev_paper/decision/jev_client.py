"""DecisionClient, JevClient, and MockClient.

Prompts ask for a qualitative label. Cutoffs that turn those labels into
actions live in config.yaml and policy/engine.py.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from jev_paper.clock import SimClock, WallClock
from jev_paper.config import JevConfig
from jev_paper.types import ChoiceAnswer, Judgment, ScoreAnswer, Snapshot

Clock = SimClock | WallClock

QUOTE_LABELS = ["No", "Marginal", "Standard", "Excellent"]
INVENTORY_LABELS = ["None", "Mild", "Skew hard", "Reduce now"]

# Instructions are qualitative on purpose. Do not add numbers here.
BATTERY: dict[str, dict[str, Any]] = {
    "regime": {
        "type": "choice",
        "instructions": "What kind of market is this snapshot in?",
        "criteria": {
            "trending": "Directional drift dominates noise",
            "mean_reverting": "Price oscillates around a stable level",
            "high_vol": "Volatility is elevated and the book is still functioning",
            "crisis": "Disorderly market with withdrawn liquidity or discontinuous moves",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": "Where is price pressure pointing over the next few minutes?",
        "criteria": {
            "up": "Buying pressure dominates",
            "down": "Selling pressure dominates",
            "neutral": "No clear directional pressure",
        },
    },
    "toxic_flow": {
        "type": "noul",
        "instructions": "Is aggressive flow currently adverse to a passive market maker?",
    },
    "liquidity_stressed": {
        "type": "noul",
        "instructions": "Is displayed liquidity too thin or too one-sided for normal quoting?",
    },
    "quote_environment": {
        "type": "score",
        "instructions": "How suitable is this book for posting passive quotes?",
        "criteria": QUOTE_LABELS,
    },
    "inventory_pressure": {
        "type": "score",
        "instructions": "How hard should a market maker lean against the current inventory?",
        "criteria": INVENTORY_LABELS,
    },
}


class DecisionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class DecisionClient(Protocol):
    model_version: str

    def judge(self, snapshot: Snapshot, *, deadline_ms: int, clock: Clock) -> Judgment:
        """Return a judgment. Stale is true when it arrives at or after deadline_ms."""


def _label(score: float, labels: list[str]) -> str:
    index = int(round(score))
    index = min(max(index, 0), len(labels) - 1)
    return labels[index]


def _choice(name: str, confidence: float, options: list[str]) -> ChoiceAnswer:
    probs = {option: 0.0 for option in options}
    probs[name] = 1.0
    return ChoiceAnswer(name, confidence, probs)


def _score(score: float, labels: list[str], confidence: float) -> ScoreAnswer:
    label = _label(score, labels)
    probs = {item: (1.0 if item == label else 0.0) for item in labels}
    return ScoreAnswer(score, label, confidence, probs)


class MockClient:
    """Deterministic stand-in used until a TypeSafe key is approved."""

    model_version = "mock-1"

    def __init__(self, latency_ms: float = 1.0, script: dict[int, Judgment] | None = None) -> None:
        self.latency_ms = latency_ms
        self.script = script or {}

    def judge(self, snapshot: Snapshot, *, deadline_ms: int, clock: Clock) -> Judgment:
        arrival = clock.now_ms() + self.latency_ms
        stale = arrival >= deadline_ms
        if snapshot.block_id in self.script:
            scripted = self.script[snapshot.block_id]
            scripted.latency_ms = self.latency_ms
            scripted.stale = stale
            scripted.model_version = self.model_version
            return scripted
        return _mock_from_block(snapshot.block_id, latency_ms=self.latency_ms, stale=stale)


def _mock_from_block(block_id: int, *, latency_ms: float, stale: bool) -> Judgment:
    if block_id % 15 == 0:
        regime = "high_vol"
    elif block_id % 2 == 0:
        regime = "mean_reverting"
    else:
        regime = "trending"
    direction = ("up", "down", "neutral")[block_id % 3]
    confidence = 0.42 if block_id % 7 == 0 else 0.8
    toxic = 0.9 if block_id % 11 == 0 else 0.2
    stressed = 0.8 if block_id % 15 == 0 else 0.15
    quote_score = 1.0 if block_id % 9 == 0 else 2.5
    inventory_score = float(block_id % 4)
    return Judgment(
        regime=_choice(regime, confidence, ["trending", "mean_reverting", "high_vol", "crisis"]),
        direction=_choice(direction, confidence, ["up", "down", "neutral"]),
        toxic_flow=toxic,
        liquidity_stressed=stressed,
        quote_environment=_score(quote_score, QUOTE_LABELS, confidence),
        inventory_pressure=_score(inventory_score, INVENTORY_LABELS, confidence),
        model_version=MockClient.model_version,
        latency_ms=latency_ms,
        stale=stale,
        raw={"source": "mock", "block_id": block_id},
    )


class JevClient:
    """Live TypeSafe call. Imported lazily so tests run without the SDK."""

    def __init__(self, model: str, timeout_s: float) -> None:
        self.model_version = model
        self.timeout_s = timeout_s

    def judge(self, snapshot: Snapshot, *, deadline_ms: int, clock: Clock) -> Judgment:
        if clock.now_ms() >= deadline_ms:
            return _empty(self.model_version, stale=True, error="deadline already passed")
        remaining_s = max(0.05, (deadline_ms - clock.now_ms()) / 1000.0)
        timeout = min(self.timeout_s, remaining_s)
        started = time.perf_counter()
        try:
            response = self._call(snapshot.compact(), timeout)
        except DecisionError:
            raise
        except Exception as exc:
            retryable = exc.__class__.__name__ in {
                "TypeSafeAPIConnectionError",
                "TypeSafeAPITimeoutError",
                "TimeoutError",
            }
            raise DecisionError(str(exc), retryable=retryable) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0
        stale = clock.now_ms() >= deadline_ms
        return _from_response(response, requested_model=self.model_version, latency_ms=latency_ms, stale=stale)

    def _call(self, state: dict[str, Any], timeout: float) -> Any:
        try:
            from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
        except ImportError as exc:
            raise DecisionError("typesafe-sdk is not installed", retryable=False) from exc
        questions = _to_sdk_questions(Choice, Noul, Score)
        client_cm = _open_client(TypeSafeClient, self.model_version, timeout)
        with client_cm as client:
            return client.system_one(state=state, questions=questions, model=self.model_version)


def _open_client(client_cls: Any, model: str, timeout: float) -> Any:
    try:
        from typesafe_sdk import RetryPolicy

        return client_cls(model=model, timeout=timeout, retry=RetryPolicy(max_retries=0, timeout=timeout))
    except Exception:
        return client_cls(model=model, timeout=timeout)


def _to_sdk_questions(choice_cls: Any, noul_cls: Any, score_cls: Any) -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for name, spec in BATTERY.items():
        kind = spec["type"]
        if kind == "choice":
            questions[name] = choice_cls(instructions=spec["instructions"], criteria=spec["criteria"])
        elif kind == "noul":
            questions[name] = noul_cls(instructions=spec["instructions"])
        else:
            questions[name] = score_cls(instructions=spec["instructions"], criteria=spec["criteria"])
    return questions


def _empty(model: str, *, stale: bool, error: str | None) -> Judgment:
    return Judgment(
        regime=None,
        direction=None,
        toxic_flow=None,
        liquidity_stressed=None,
        quote_environment=None,
        inventory_pressure=None,
        model_version=model,
        latency_ms=0.0,
        stale=stale,
        error=error,
    )


def _read(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _from_response(response: Any, *, requested_model: str, latency_ms: float, stale: bool) -> Judgment:
    model = str(_read(response, "model", None) or requested_model)
    raw: dict[str, Any]
    if hasattr(response, "model_dump"):
        raw = response.model_dump()
    elif isinstance(response, dict):
        raw = response
    else:
        raw = {"repr": repr(response)}
    regime = _parse_choice(_lookup(response, "choices", "regime"))
    direction = _parse_choice(_lookup(response, "choices", "direction"))
    toxic = _parse_noul(_lookup(response, "nouls", "toxic_flow"))
    stressed = _parse_noul(_lookup(response, "nouls", "liquidity_stressed"))
    quote = _parse_score(_lookup(response, "scores", "quote_environment"), QUOTE_LABELS)
    inventory = _parse_score(_lookup(response, "scores", "inventory_pressure"), INVENTORY_LABELS)
    return Judgment(
        regime=regime,
        direction=direction,
        toxic_flow=toxic,
        liquidity_stressed=stressed,
        quote_environment=quote,
        inventory_pressure=inventory,
        model_version=model,
        latency_ms=latency_ms,
        stale=stale,
        raw=raw,
    )


def _lookup(response: Any, group: str, name: str) -> Any:
    grouped = _read(response, group, None)
    if grouped is not None:
        found = _read(grouped, name, None)
        if found is not None:
            return found
    answers = _read(response, "answers", None)
    if answers is None:
        return None
    return _read(answers, name, None)


def _parse_choice(value: Any) -> ChoiceAnswer | None:
    if value is None:
        return None
    name = _read(value, "choice", None)
    if name is None:
        return None
    confidence = float(_read(value, "confidence", 0.0) or 0.0)
    probabilities = dict(_read(value, "probabilities", {}) or {})
    return ChoiceAnswer(str(name), confidence, {str(k): float(v) for k, v in probabilities.items()})


def _parse_noul(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    number = _read(value, "noul", None)
    if number is None:
        return None
    return float(number)


def _parse_score(value: Any, labels: list[str]) -> ScoreAnswer | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        score = float(value)
        return _score(score, labels, 0.0)
    number = _read(value, "score", None)
    if number is None:
        return None
    confidence = float(_read(value, "confidence", 0.0) or 0.0)
    probabilities = dict(_read(value, "probabilities", {}) or {})
    parsed = ScoreAnswer(
        float(number),
        _label(float(number), labels),
        confidence,
        {str(k): float(v) for k, v in probabilities.items()},
    )
    return parsed


def build_client(config: JevConfig) -> DecisionClient:
    if config.client == "mock":
        return MockClient()
    if config.client == "jev":
        return JevClient(model=config.model, timeout_s=config.timeout_s)
    raise ValueError(f"unknown jev client {config.client!r}")
