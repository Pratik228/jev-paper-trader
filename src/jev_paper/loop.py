"""Paper loop. State, then a judgment, then a policy, then a risk veto.

Stale judgment: hold resting quotes.
Jev down: code-only rules.
Hard limit breach: flatten and alert.
"""

from __future__ import annotations

import argparse
import queue
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from jev_paper.alerts.telegram import Alerter, alerter_from_env
from jev_paper.clock import SimClock, WallClock
from jev_paper.config import AppConfig, load_config
from jev_paper.data.feed import LiveFeed, read_events
from jev_paper.decision.jev_client import DecisionClient, DecisionError, build_client
from jev_paper.health import HealthState, serve
from jev_paper.jsonlog import append_jsonl
from jev_paper.policy.engine import PolicyEngine
from jev_paper.pricing.avellaneda import quotes_for
from jev_paper.risk.engine import RiskEngine
from jev_paper.sim.fills import PaperAccount, flatten, mark, match_trade, sync_quotes
from jev_paper.state.engine import StateEngine
from jev_paper.strategies import SPECS, StrategySpec
from jev_paper.types import AccountView, Action, ActionDecision, BookEvent, Event, Snapshot, TradeEvent


@dataclass
class Touch:
    bid: float | None = None
    ask: float | None = None
    ts_ms: int | None = None


def run_replay(
    events: Iterable[Event],
    config: AppConfig,
    log_dir: Path,
    *,
    client: DecisionClient | None = None,
    alerter: Alerter | None = None,
) -> None:
    stream = _Peek(iter(events))
    first = stream.peek()
    if first is None:
        return
    engine = StateEngine(config.state)
    accounts = {spec.name: PaperAccount() for spec in SPECS}
    policy = PolicyEngine(config.policy)
    risk = RiskEngine(config.risk)
    client = client or build_client(config.jev)
    clock = SimClock(first.ts_ms)
    touch = Touch()
    paths = _paths(log_dir)
    block_id = 0
    t = first.ts_ms
    while stream.peek() is not None or t <= stream.last_ts:
        _fold(engine, stream, t, touch, accounts, config, match=False)
        clock.t = t
        _decide_all(
            engine, accounts, policy, risk, client, config, clock, touch, paths, block_id, t, alerter
        )
        next_t = t + config.jev.block_ms
        _fold(engine, stream, next_t, touch, accounts, config, match=True)
        t = next_t
        block_id += 1


def run_live(config: AppConfig, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    alerter = alerter_from_env(
        log_dir / "alerts.jsonl",
        config.alerts.telegram_bot_token_env,
        config.alerts.telegram_chat_id_env,
    )
    health = HealthState()
    server = serve(health, config.health.host, config.health.port)
    feed = LiveFeed(config.feed)
    pending: queue.Queue[Event] = queue.Queue()
    worker_started = False
    try:
        import threading

        thread = threading.Thread(target=feed.run, args=(pending.put,), name="feed", daemon=True)
        thread.start()
        worker_started = True
        _live_loop(config, log_dir, pending, alerter, health)
    except Exception as exc:
        alerter.send(f"paper bot crashed: {exc.__class__.__name__}: {exc}")
        raise
    finally:
        feed.stop()
        if worker_started:
            server.shutdown()


def _live_loop(
    config: AppConfig,
    log_dir: Path,
    pending: queue.Queue[Event],
    alerter: Alerter,
    health: HealthState,
) -> None:
    engine = StateEngine(config.state)
    accounts = {spec.name: PaperAccount() for spec in SPECS}
    policy = PolicyEngine(config.policy)
    risk = RiskEngine(config.risk)
    client = build_client(config.jev)
    clock = WallClock()
    touch = Touch()
    paths = _paths(log_dir)
    time.sleep(config.jev.block_ms / 1000.0)
    _drain(engine, pending, touch, accounts, config, match=False)
    block_id = 0
    while True:
        now = clock.now_ms()
        _decide_all(
            engine, accounts, policy, risk, client, config, clock, touch, paths, block_id, now, alerter, health
        )
        time.sleep(config.jev.block_ms / 1000.0)
        _drain(engine, pending, touch, accounts, config, match=True)
        block_id += 1


def _drain(
    engine: StateEngine,
    pending: queue.Queue[Event],
    touch: Touch,
    accounts: dict[str, PaperAccount],
    config: AppConfig,
    *,
    match: bool,
) -> None:
    batch: list[Event] = []
    while True:
        try:
            batch.append(pending.get_nowait())
        except queue.Empty:
            break
    for event in batch:
        if match and isinstance(event, TradeEvent):
            for account in accounts.values():
                if not account.halted:
                    account.pending_events.extend(match_trade(account, event, config.fees))
        _note(touch, event)
        engine.update(event)


class _Peek:
    """One-event lookahead over a stream, so replays never hold a whole tape in memory."""

    def __init__(self, source: Iterator[Event]) -> None:
        self._source = source
        self._next: Event | None = None
        self._done = False
        self.last_ts = -1

    def peek(self) -> Event | None:
        if self._next is None and not self._done:
            self._next = next(self._source, None)
            self._done = self._next is None
        return self._next

    def pop(self) -> Event:
        event = self.peek()
        assert event is not None
        self._next = None
        self.last_ts = event.ts_ms
        return event


def _fold(
    engine: StateEngine,
    stream: _Peek,
    until_ms: int,
    touch: Touch,
    accounts: dict[str, PaperAccount],
    config: AppConfig,
    *,
    match: bool,
) -> None:
    while (head := stream.peek()) is not None and head.ts_ms <= until_ms:
        event = stream.pop()
        if match and isinstance(event, TradeEvent):
            for account in accounts.values():
                if not account.halted:
                    account.pending_events.extend(match_trade(account, event, config.fees))
        _note(touch, event)
        engine.update(event)


def _note(touch: Touch, event: Event) -> None:
    if isinstance(event, BookEvent) and event.bids and event.asks:
        touch.bid = event.bids[0].price
        touch.ask = event.asks[0].price
        touch.ts_ms = event.ts_ms


def _decide_all(
    engine: StateEngine,
    accounts: dict[str, PaperAccount],
    policy: PolicyEngine,
    risk: RiskEngine,
    client: DecisionClient,
    config: AppConfig,
    clock: SimClock | WallClock,
    touch: Touch,
    paths: dict[str, Path],
    block_id: int,
    ts_ms: int,
    alerter: Alerter | None,
    health: HealthState | None = None,
) -> None:
    clock_now = clock.now_ms()
    base = engine.snapshot(ts_ms, block_id, AccountView(0.0, 0.0, 0.0))
    summary: dict[str, dict[str, object]] = {}
    for spec in SPECS:
        account = accounts[spec.name]
        summary[spec.name] = _decide_one(
            spec, account, base, policy, risk, client, config, clock, touch, paths, ts_ms, clock_now, alerter
        )
    if health is not None:
        age = None if touch.ts_ms is None else max(0, clock_now - touch.ts_ms)
        health.update(
            {
                "ok": True,
                "ready": base.mid is not None,
                "block_id": block_id,
                "ts_ms": ts_ms,
                "data_age_ms": age,
                "mid": base.mid,
                "client": config.jev.client,
                "model": client.model_version,
                "strategies": summary,
            }
        )


def _decide_one(
    spec: StrategySpec,
    account: PaperAccount,
    base: Snapshot,
    policy: PolicyEngine,
    risk: RiskEngine,
    client: DecisionClient,
    config: AppConfig,
    clock: SimClock | WallClock,
    touch: Touch,
    paths: dict[str, Path],
    ts_ms: int,
    clock_now: int,
    alerter: Alerter | None,
) -> dict[str, object]:
    mid = base.mid or touch.bid or touch.ask or 0.0
    carried = account.pending_events
    account.pending_events = []
    if mid:
        mark(account, mid, ts_ms)
    view = AccountView(account.inventory, account.unrealized(mid) if mid else 0.0, account.drawdown(mid) if mid else 0.0)
    snapshot = _with_account(base, view)
    append_jsonl(paths["snapshots"], {"strategy": spec.name, **snapshot.compact()})
    if account.halted:
        _log_outcome(paths, spec.name, snapshot, ActionDecision(Action.HOLD, "halted", "risk"), None, account, mid, carried)
        return {"halted": True, "inventory": account.inventory, "equity": account.equity(mid)}

    decision, latency = _choose_action(spec, snapshot, policy, client, config, clock, account, paths)
    data_age = None if touch.ts_ms is None else max(0, clock_now - touch.ts_ms)
    verdict = risk.evaluate(
        position_usd=abs(account.inventory * mid) if mid else 0.0,
        daily_pnl=account.daily_pnl(mid) if mid else 0.0,
        drawdown=account.drawdown(mid) if mid else 0.0,
        data_age_ms=data_age,
        decision_latency_ms=latency,
        consecutive_api_errors=account.consecutive_api_errors,
    )
    events: list[dict[str, float | str]] = []
    if decision.action == Action.KILL or verdict.flatten:
        reason = "kill" if decision.action == Action.KILL else (verdict.reason or "limit")
        events = flatten(account, touch.bid, touch.ask, config.fees)
        _alert(alerter, account, f"{spec.name} flatten: {reason}")
        decision = ActionDecision(Action.KILL, reason, "risk" if verdict.flatten else decision.source)
    elif verdict.reduce_only:
        bid, ask = quotes_for(spec.pricer, snapshot, _quote_decision(decision), config.pricing, config.risk)
        if account.inventory > 0:
            bid = None
        elif account.inventory < 0:
            ask = None
        events = sync_quotes(account, bid, ask, config.fees, mid=mid, requote_bps=config.pricing.requote_bps)
        _alert(alerter, account, f"{spec.name} reduce only: max position")
        decision = ActionDecision(Action.QUOTE_BOTH_SIDES, "reduce only", "risk", decision.skew_bps, decision.spread_multiplier)
    elif not verdict.allow_quotes and verdict.reason == "decision latency":
        decision = ActionDecision(Action.HOLD, "decision latency", "risk")
    elif not verdict.allow_quotes or decision.action in {Action.PULL_QUOTES, Action.STAND_DOWN}:
        events = sync_quotes(account, None, None, config.fees, mid=mid, requote_bps=0.0)
        if verdict.alert:
            _alert(alerter, account, f"{spec.name} veto: {verdict.reason or decision.reason}")
    elif decision.action != Action.HOLD:
        bid, ask = quotes_for(spec.pricer, snapshot, decision, config.pricing, config.risk)
        events = sync_quotes(
            account, bid, ask, config.fees, mid=mid, requote_bps=config.pricing.requote_bps
        )
    if mid:
        mark(account, mid, ts_ms)
    _log_outcome(paths, spec.name, snapshot, decision, verdict.reason, account, mid, carried + events)
    return {
        "halted": account.halted,
        "inventory": account.inventory,
        "equity": account.equity(mid) if mid else account.cash,
        "action": decision.action.value,
    }


def _quote_decision(decision: ActionDecision) -> ActionDecision:
    if decision.action in {Action.HOLD, Action.PULL_QUOTES, Action.STAND_DOWN, Action.KILL}:
        return ActionDecision(Action.QUOTE_BOTH_SIDES, decision.reason, decision.source, decision.skew_bps, 1.0)
    return decision


def _choose_action(
    spec: StrategySpec,
    snapshot: Snapshot,
    policy: PolicyEngine,
    client: DecisionClient,
    config: AppConfig,
    clock: SimClock | WallClock,
    account: PaperAccount,
    paths: dict[str, Path],
) -> tuple[ActionDecision, float | None]:
    every = max(1, config.jev.call_every_n_blocks)
    if not spec.uses_jev:
        return policy.code_only(snapshot), None
    if snapshot.block_id % every != 0:
        return ActionDecision(Action.HOLD, "between jev calls", "schedule"), None
    deadline = clock.now_ms() + config.jev.block_ms
    try:
        judgment = client.judge(snapshot, deadline_ms=deadline, clock=clock)
    except DecisionError as exc:
        account.consecutive_api_errors += 1
        append_jsonl(
            paths["decisions"],
            {
                "strategy": spec.name,
                "block_id": snapshot.block_id,
                "ts_ms": snapshot.ts_ms,
                "model_version": client.model_version,
                "error": str(exc),
                "stale": False,
            },
        )
        return policy.code_only(snapshot), None
    append_jsonl(
        paths["decisions"],
        {"strategy": spec.name, "block_id": snapshot.block_id, "ts_ms": snapshot.ts_ms, **judgment.to_dict()},
    )
    if judgment.stale:
        return ActionDecision(Action.HOLD, "stale judgment", "hold"), judgment.latency_ms
    if judgment.error:
        account.consecutive_api_errors += 1
        return policy.code_only(snapshot), judgment.latency_ms
    account.consecutive_api_errors = 0
    return policy.from_judgment(snapshot, judgment, gated=spec.gated), judgment.latency_ms


def _with_account(base: Snapshot, view: AccountView) -> Snapshot:
    return replace(
        base,
        inventory=view.inventory,
        unrealized_pnl=view.unrealized_pnl,
        drawdown=view.drawdown,
    )


def _alert(alerter: Alerter | None, account: PaperAccount, text: str) -> None:
    if alerter is None:
        return
    if account.last_alert == text:
        return
    account.last_alert = text
    alerter.send(text)


def _log_outcome(
    paths: dict[str, Path],
    name: str,
    snapshot: Snapshot,
    decision: ActionDecision,
    veto: str | None,
    account: PaperAccount,
    mid: float,
    events: list[dict[str, float | str]],
) -> None:
    record = {
        "strategy": name,
        "block_id": snapshot.block_id,
        "ts_ms": snapshot.ts_ms,
        "mid": snapshot.mid,
        "decision": decision.to_dict(),
        "veto": veto,
        "bid": account.bid.price if account.bid else None,
        "ask": account.ask.price if account.ask else None,
        "inventory": account.inventory,
        "cash": account.cash,
        "equity": account.equity(mid) if mid else account.cash,
        "realized_pnl": account.realized_pnl,
        "unrealized_pnl": account.unrealized(mid) if mid else 0.0,
        "fees_paid": account.fees_paid,
        "gas_paid": account.gas_paid,
        "drawdown": account.drawdown(mid) if mid else 0.0,
        "halted": account.halted,
        "events": events,
    }
    append_jsonl(paths["outcomes"], record)
    append_jsonl(paths[name], record)


def _paths(log_dir: Path) -> dict[str, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "snapshots": log_dir / "snapshots.jsonl",
        "decisions": log_dir / "decisions.jsonl",
        "outcomes": log_dir / "outcomes.jsonl",
        "alerts": log_dir / "alerts.jsonl",
        **{spec.name: log_dir / f"outcomes_{spec.name}.jsonl" for spec in SPECS},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper market-making loop. Never places live orders.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--log-dir", default="")
    parser.add_argument("--replay", default="")
    parser.add_argument("--record", action="store_true", help="Record public market data only. No strategy.")
    args = parser.parse_args()
    config = load_config(args.config)
    log_dir = Path(args.log_dir or config.log_dir)
    if args.record:
        from jev_paper.data.record import run_record

        run_record(Path(args.config), Path(config.data_dir))
        return
    if args.replay:
        run_replay(read_events(args.replay), config, log_dir)
        return
    run_live(config, log_dir)


if __name__ == "__main__":
    main()
