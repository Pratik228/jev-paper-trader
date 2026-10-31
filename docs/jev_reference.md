# Jev reference for this repo

Cursor does not load the TypeSafe Claude Code plugin. This file is the local copy of the SDK shape the paper bot calls. Official docs: <https://docs.typesafe.ai>. Python SDK: <https://docs.typesafe.ai/sdk/python>.

Jev is a decision model. One call sends a state plus typed questions and returns constrained answers. It does not write the order.

## Install

```bash
pip install typesafe-sdk
export TYPESAFE_API_KEY=...   # server-side only, never commit it
```

Set a spend cap on the TypeSafe account before pointing `config.yaml` at `client: jev`.

## Call

Pinned model. This repo passes `model` from `config.yaml` (`jev-1.14.0`), not `jev-latest`.

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

with TypeSafeClient(model="jev-1.14.0", timeout=0.8) as client:
    response = client.system_one(
        state={"mid": 100.0, "spread_bps": 2.0, "inventory": 0.0},
        questions={
            "regime": Choice(
                instructions="What kind of market is this snapshot in?",
                criteria={
                    "trending": "Directional drift dominates noise",
                    "mean_reverting": "Price oscillates around a stable level",
                    "high_vol": "Volatility is elevated and the book is still functioning",
                    "crisis": "Disorderly market with withdrawn liquidity or discontinuous moves",
                },
            ),
            "toxic_flow": Noul(
                instructions="Is aggressive flow currently adverse to a passive market maker?",
            ),
            "quote_environment": Score(
                instructions="How suitable is this book for posting passive quotes?",
                criteria=["No", "Marginal", "Standard", "Excellent"],
            ),
        },
        model="jev-1.14.0",
    )

print(response.model)                          # resolved model version, log this
print(response.choices["regime"].choice)       # label
print(response.choices["regime"].confidence)   # 0 to 1
print(response.nouls["toxic_flow"].noul)       # probability of yes, 0 to 1
print(response.scores["quote_environment"].score)
```

The same three primitives are `Choice`, `Noul`, and `Score`. A Noul has no separate confidence; the number is the yes probability. Thresholds that turn these numbers into actions belong in `config.yaml`, not in `instructions`.

HTTP equivalent: `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer $TYPESAFE_API_KEY`.

```json
{
  "model": "jev-1.14.0",
  "state": {"mid": 100.0},
  "questions": {
    "toxic_flow": {
      "type": "noul",
      "instructions": "Is aggressive flow currently adverse to a passive market maker?"
    }
  }
}
```

The response includes `model`, `answers`, and `usage`. Latency is not a response field. This repo times the call itself.

## Where it is used

`src/jev_paper/decision/jev_client.py` is the only module that imports `typesafe_sdk`. `JevClient` implements `DecisionClient`. `MockClient` implements the same interface and is the default until a key exists. A judgment that arrives at or after the next block is stored and marked `stale`. The loop does not trade on it.
