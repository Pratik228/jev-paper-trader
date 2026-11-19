# Contributing

Issues and pull requests are welcome.

## Ground rules

- **Paper only.** Nothing in this repo may place a live order, hold a private key, or call an order endpoint. `tests/test_no_live_orders.py` enforces this, and PRs that weaken it will not be merged.
- **No secrets in the repo.** Keys go in `.env` (gitignored). `.env.example` lists the names.
- **Numbers need a tape.** If a PR claims a strategy improvement, include the command, the tape (dates or a fixture), and the before/after table from `scripts/summarize_replay.py`.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
make test
```

## Good first areas

- A new `DecisionClient` (the interface in `src/jev_paper/decision/jev_client.py`). Any model that answers the six questions can sit in the Jev slot and be compared against `code_only` on the same tape.
- Queue-position modelling in `src/jev_paper/sim/fills.py`. Today a trade at or through the quote fills us as if we were first in the queue, which is optimistic.
- Fee tiers and venues in `config.yaml`.

Keep PRs small, keep `make test` green, and add a test for any behaviour change.
