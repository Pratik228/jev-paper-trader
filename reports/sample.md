<!-- analyst engine: linear; narrative: template -->

# Paper post-mortem sample

This note is computed from the JSONL logs. It is not a live track record.

## PnL after fees
- jev_gated: net 18.3254, gross 50.9725, fees 32.6471, gas 0.0000, fee share of |gross| 64.05%, fills 220
- jev_ungated: net 17.4147, gross 48.5754, fees 31.1607, gas 0.0000, fee share of |gross| 64.15%, fills 211
- baseline: net -22.6787, gross 25.1770, fees 47.8557, gas 0.0000, fee share of |gross| 190.08%, fills 349

## Calibration

- confidence 0.0-0.2: n=0 hit rate n/a
- confidence 0.2-0.4: n=0 hit rate n/a
- confidence 0.4-0.6: n=45 hit rate 44.44%
- confidence 0.6-0.8: n=0 hit rate n/a
- confidence 0.8-1.0: n=267 hit rate 51.31%

## Regimes

- high_vol: pnl 7.6588 over 1600 blocks
- no_judgment: pnl -22.6787 over 2400 blocks
- trending: pnl 15.2450 over 1600 blocks
- mean_reverting: pnl 12.8364 over 1600 blocks

## Latency and stale decisions

- stale rate 0.00% on 960 logged calls
- latency <50: 960
- latency <100: 0
- latency <250: 0
- latency <500: 0
- latency <1000: 0
- latency >=1000: 0

Proposed threshold edits, if any, are in the matching proposals diff and were not applied.
